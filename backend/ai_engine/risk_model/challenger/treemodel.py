"""Pure-Python/NumPy scorer for a saved LightGBM text model (``model.txt``): raw scores and exact TreeSHAP.

Why: LightGBM imports SciPy at load time, which with LightGBM adds ~120 MB to a deployment that only needs to score
a few dozen small trees. Serverless bundles (Vercel's 500 MB Python limit) cannot afford that, so the challenger
falls back to this scorer when ``lightgbm`` is not installed. It reproduces LightGBM's own algorithms:

* routing — ``Tree::NumericalDecision`` (missing types None/Zero/NaN, default-left bit, ``<=`` thresholds);
* raw score — the sum of leaf outputs over all trees (leaf values in model.txt already include shrinkage);
* contributions — ``Tree::PredictContrib`` / ``Tree::TreeSHAP`` (Lundberg et al.'s path-dependent TreeSHAP over
  ``internal_count`` / ``leaf_count``), with the expected value in the last column like ``pred_contrib=True``.

``tests/test_challenger.py`` checks it against LightGBM itself on the shipped artifact.
Only numerical splits of binary/regression GBDT models are supported (that is what the training pipeline produces);
anything else raises ``ValueError`` so the caller reports the challenger as unavailable instead of mis-scoring.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_ZERO = 1e-35  # LightGBM kZeroThreshold
_MISSING_NONE, _MISSING_ZERO, _MISSING_NAN = 0, 1, 2


@dataclass
class _Tree:
    split_feature: list[int]
    threshold: list[float]
    decision_type: list[int]
    left: list[int]
    right: list[int]
    leaf_value: list[float]
    leaf_count: list[float]
    internal_count: list[float]

    @property
    def num_leaves(self) -> int:
        return len(self.leaf_value)

    def _decide(self, x: np.ndarray, node: int) -> int:
        fval = float(x[self.split_feature[node]])
        dt = self.decision_type[node]
        if dt & 1:
            raise ValueError("categorical splits are not supported")
        missing = (dt >> 2) & 3
        if math.isnan(fval) and missing != _MISSING_NAN:
            fval = 0.0
        if (missing == _MISSING_ZERO and -_ZERO <= fval <= _ZERO) or (missing == _MISSING_NAN and math.isnan(fval)):
            return self.left[node] if dt & 2 else self.right[node]
        return self.left[node] if fval <= self.threshold[node] else self.right[node]

    def predict(self, x: np.ndarray) -> float:
        if self.num_leaves == 1:
            return self.leaf_value[0]
        node = 0
        while node >= 0:
            node = self._decide(x, node)
        return self.leaf_value[~node]

    # -- TreeSHAP (port of LightGBM src/io/tree.cpp) -------------------------------------------------------
    def _count(self, node: int) -> float:
        return self.internal_count[node] if node >= 0 else self.leaf_count[~node]

    def expected_value(self) -> float:
        if self.num_leaves == 1:
            return self.leaf_value[0]
        total = self.internal_count[0]
        return sum(c / total * v for c, v in zip(self.leaf_count, self.leaf_value))

    def contrib(self, x: np.ndarray, phi: np.ndarray) -> None:
        phi[-1] += self.expected_value()
        if self.num_leaves > 1:
            self._shap(x, phi, 0, 0, [], 1.0, 1.0, -1)

    def _shap(self, x, phi, node, depth, parent, zero_fraction, one_fraction, feature) -> None:
        path = [list(e) for e in parent[:depth]] + [[0, 0.0, 0.0, 0.0]]  # [feature, zero, one, pweight]
        _extend(path, depth, zero_fraction, one_fraction, feature)
        if node < 0:
            value = self.leaf_value[~node]
            for i in range(1, depth + 1):
                w = _unwound_sum(path, depth, i)
                phi[path[i][0]] += w * (path[i][2] - path[i][1]) * value
            return
        hot = self._decide(x, node)
        cold = self.right[node] if hot == self.left[node] else self.left[node]
        w = self._count(node)
        hot_zero, cold_zero = self._count(hot) / w, self._count(cold) / w
        incoming_zero = incoming_one = 1.0
        split = self.split_feature[node]
        k = next((i for i in range(depth + 1) if path[i][0] == split), None)
        if k is not None:
            incoming_zero, incoming_one = path[k][1], path[k][2]
            _unwind(path, depth, k)
            depth -= 1
        self._shap(x, phi, hot, depth + 1, path, hot_zero * incoming_zero, incoming_one, split)
        self._shap(x, phi, cold, depth + 1, path, cold_zero * incoming_zero, 0.0, split)


def _extend(path, depth, zero_fraction, one_fraction, feature) -> None:
    path[depth] = [feature, zero_fraction, one_fraction, 1.0 if depth == 0 else 0.0]
    for i in range(depth - 1, -1, -1):
        path[i + 1][3] += one_fraction * path[i][3] * (i + 1) / (depth + 1)
        path[i][3] = zero_fraction * path[i][3] * (depth - i) / (depth + 1)


def _unwind(path, depth, index) -> None:
    one, zero = path[index][2], path[index][1]
    next_one = path[depth][3]
    for i in range(depth - 1, -1, -1):
        if one != 0:
            tmp = path[i][3]
            path[i][3] = next_one * (depth + 1) / ((i + 1) * one)
            next_one = tmp - path[i][3] * zero * (depth - i) / (depth + 1)
        else:
            path[i][3] = path[i][3] * (depth + 1) / (zero * (depth - i))
    for i in range(index, depth):
        path[i][0], path[i][1], path[i][2] = path[i + 1][0], path[i + 1][1], path[i + 1][2]


def _unwound_sum(path, depth, index) -> float:
    one, zero = path[index][2], path[index][1]
    next_one = path[depth][3]
    total = 0.0
    for i in range(depth - 1, -1, -1):
        if one != 0:
            tmp = next_one * (depth + 1) / ((i + 1) * one)
            total += tmp
            next_one = path[i][3] - tmp * zero * ((depth - i) / (depth + 1))
        elif zero != 0:
            total += (path[i][3] / zero) / ((depth - i) / (depth + 1))
    return total


class TextModelBooster:
    """The subset of ``lightgbm.Booster`` the challenger uses: ``feature_name()`` and ``predict(raw_score|pred_contrib)``."""

    def __init__(self, model_file: str | Path):
        text = Path(model_file).read_text()
        header, _, body = text.partition("\nTree=")
        meta = dict(line.split("=", 1) for line in header.splitlines() if "=" in line)
        if int(meta.get("num_class", "1")) != 1 or int(meta.get("num_tree_per_iteration", "1")) != 1:
            raise ValueError("only single-output models are supported")
        self._features = meta["feature_names"].split()
        self.trees: list[_Tree] = []
        for block in ("Tree=" + body).split("\nTree=")[0:]:
            block = block.split("end of trees")[0]
            kv = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            if "leaf_value" not in kv:
                continue
            if int(kv.get("num_cat", "0")) or kv.get("is_linear", "0") != "0":
                raise ValueError("categorical or linear trees are not supported")
            n_leaves = int(kv["num_leaves"])
            ints = lambda k: [int(v) for v in kv.get(k, "").split()]  # noqa: E731
            floats = lambda k: [float(v) for v in kv.get(k, "").split()]  # noqa: E731
            tree = _Tree(ints("split_feature"), floats("threshold"), ints("decision_type"), ints("left_child"),
                         ints("right_child"), floats("leaf_value"), floats("leaf_count"), floats("internal_count"))
            if len(tree.leaf_value) != n_leaves or (n_leaves > 1 and len(tree.split_feature) != n_leaves - 1):
                raise ValueError("malformed tree in model file")
            self.trees.append(tree)
        if not self.trees:
            raise ValueError("model file contains no trees")

    def feature_name(self) -> list[str]:
        return list(self._features)

    def predict(self, X, raw_score: bool = False, pred_contrib: bool = False) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        if X.ndim == 1:
            X = X[None, :]
        if pred_contrib:
            out = np.zeros((X.shape[0], len(self._features) + 1))
            for r, x in enumerate(X):
                for t in self.trees:
                    t.contrib(x, out[r])
            return out
        raw = np.array([sum(t.predict(x) for t in self.trees) for x in X])
        return raw if raw_score else 1.0 / (1.0 + np.exp(-raw))
