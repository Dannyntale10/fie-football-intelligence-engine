import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        pitch: "#07120c",
        panel: "#0d1f16",
        edge: "#1d3a2a",
        lime: "#a3e635",
        mist: "#d1fae5",
      },
    },
  },
  plugins: [],
};

export default config;
