/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // `standalone` traces only the server files the runtime needs, which keeps the
  // Docker image small. It is opt-in via the build arg set in
  // docker/frontend.Dockerfile, because `next start` does not support it - so a
  // plain local `npm run build && npm run start` still works.
  output: process.env.NEXT_OUTPUT_STANDALONE === "1" ? "standalone" : undefined,
};

export default nextConfig;
