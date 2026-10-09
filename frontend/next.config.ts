import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // The chat widget is meant to be embedded in TokTok's web/app webviews.
  async headers() {
    const ancestors = process.env.EMBED_ALLOWED_ORIGINS ?? "'self' https://www.toktok.mn https://toktok.mn";
    return [
      {
        source: "/embed",
        headers: [{ key: "Content-Security-Policy", value: `frame-ancestors ${ancestors}` }],
      },
    ];
  },
};

export default nextConfig;
