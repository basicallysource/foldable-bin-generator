/** @type {import('next').NextConfig} */
const nextConfig = {
  // In dev, /api/* is proxied to the local Flask process (api/index.py on
  // :5328). In production on Vercel, api/index.py is built as a Python
  // serverless function; the rewrite funnels every /api/* path to it and the
  // Flask app does its own routing (official Next.js + Flask pattern).
  rewrites: async () => [
    {
      source: "/api/:path*",
      destination:
        process.env.NODE_ENV === "development"
          ? "http://127.0.0.1:5328/api/:path*"
          : "/api/",
    },
  ],
};

export default nextConfig;
