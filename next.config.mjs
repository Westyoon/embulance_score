/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  outputFileTracingExcludes: {
    "/api/*": ["./data/**/*", "./src/data/**/*", ".env", ".env.*"],
  },
};

export default nextConfig;
