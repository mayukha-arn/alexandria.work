// A fully client-side app (it talks to the Alexandria API from the browser), so it can be
// exported as static files and hosted anywhere: Azure Static Web Apps, GitHub Pages, a bucket.
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
};
export default nextConfig;
