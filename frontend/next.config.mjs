/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // `standalone` keeps the production Docker image small: only the traced
  // server files and their dependencies are copied into the runtime stage.
  output: "standalone",
};

export default nextConfig;
