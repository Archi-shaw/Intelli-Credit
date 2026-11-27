"use client";

import { useEffect } from "react";

import { Button, Callout } from "@/components/ui/primitives";

/** Route-level error boundary: a rendering error shows a recoverable message instead of a blank page. */
export default function AppError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => { console.error(error); }, [error]);
  return (
    <div className="mx-auto max-w-xl py-16">
      <Callout tone="danger" title="This page could not be displayed">
        <p>An unexpected error occurred while rendering. Your data has not been changed.</p>
        {error.digest && <p className="mt-1 font-mono text-xs">Reference: {error.digest}</p>}
      </Callout>
      <div className="mt-4 flex gap-2"><Button onClick={reset}>Try again</Button><Button variant="outline" onClick={() => (window.location.href = "/")}>Go to dashboard</Button></div>
    </div>
  );
}
