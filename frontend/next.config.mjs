/** @type {import('next').NextConfig} */
const backend = process.env.BACKEND_URL || "http://127.0.0.1:8001";
// The /api proxy otherwise aborts after Next's default of 30 s. Assistant chat and research call external
// providers synchronously (LLM_TIMEOUT_SECONDS defaults to 90 s, with SDK retries), so allow up to 180 s.
// Keep reverse-proxy read timeouts (e.g. nginx proxy_read_timeout) at least this long.
const proxyTimeoutMs = Number(process.env.API_PROXY_TIMEOUT_MS || 180000);
// Next buffers request bodies for rewrites and silently truncates them at 10 MB by default, which made uploads
// between 10 MB and the API's MAX_UPLOAD_MB (25) hang until timeout. Allow the API limit plus multipart overhead;
// the API itself still enforces MAX_UPLOAD_MB.
const proxyMaxBodyMb = Number(process.env.API_PROXY_MAX_BODY_MB || 26);

const nextConfig = {
  experimental: { proxyTimeout: proxyTimeoutMs, middlewareClientMaxBodySize: `${proxyMaxBodyMb}mb` },
  // A separate dist dir lets the E2E production build run alongside `next dev`.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // Self-contained server for the Docker image; Vercel builds (VERCEL=1) use the platform's own Next.js output.
  output: process.env.VERCEL ? undefined : "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  // The browser only ever talks to this origin; API calls are proxied server-side so the session
  // cookie stays first-party and the backend URL is never exposed to the client bundle.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
