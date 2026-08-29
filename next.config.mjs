/** @type {import('next').NextConfig} */
const nextConfig = process.env.DEPLOY_TARGET === "github-pages"
  ? {
      output: "export",
      basePath: process.env.PAGES_BASE_PATH ?? "",
    }
  : {};

export default nextConfig;
