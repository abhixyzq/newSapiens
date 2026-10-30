/** API requests are proxied same-origin so the HttpOnly auth cookie works without CORS. */
const backend = process.env.BACKEND_URL || "http://127.0.0.1:8000";
export default {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};
