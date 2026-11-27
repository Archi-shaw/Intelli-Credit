"""Regression tests for extraction edge cases found while building the platform."""
import time

from ai_engine.extraction.financial_extractor import PageContent, _TRAILING_NUMS, extract_document


def test_trailing_number_regex_is_linear_on_number_dense_lines():
    # Previously exponential: many numbers followed by a non-numeric tail (e.g. a date) hung the worker.
    line = "GSTR-3B    2023-05    27AAKCS9001P1Z5" + "    1980000.00" * 12 + "    2023-06-19"
    t = time.perf_counter()
    assert _TRAILING_NUMS.search(line) is None
    assert time.perf_counter() - t < 0.5
    text = "\n".join([line] * 50)
    t = time.perf_counter()
    extract_document([PageContent(1, text)], "gst_returns")
    assert time.perf_counter() - t < 2.0


def test_negative_values_in_brackets_and_lakh_scaling():
    page = PageContent(1, "(All amounts in ₹ Lakhs)\nParticulars    FY 2023-24    FY 2022-23\n"
                          "Profit before tax    (108.40)    (44.90)\nTotal equity    (62.70)    45.70\n")
    got = {(m.metric_key, m.period_label): m.value for m in extract_document([page], "financial_statements").metrics}
    assert got[("pbt", "FY2023-24")] == -10840000.0
    assert got[("shareholders_equity", "FY2022-23")] == 4570000.0
