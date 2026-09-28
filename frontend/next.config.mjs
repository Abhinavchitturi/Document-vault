// The vault ships as static files that the Python server serves (one process, no Node at run time):
//   npm run build  ->  frontend/out/, served by `python -m backend.api.app`
// During development, `npm run dev` serves the pages on :3000 and forwards /api to the Python server on :8000.
const isDev = process.env.NODE_ENV !== "production";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: isDev ? undefined : "export",
  trailingSlash: true,
  images: { unoptimized: true },
  poweredByHeader: false,
  ...(isDev && {
    async rewrites() {
      return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }];
    },
  }),
};

export default nextConfig;
