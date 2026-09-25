import { Plus_Jakarta_Sans, Syne } from "next/font/google";

// Shared by both root layouts: (site) for the mini-app and landing page,
// (admin) for the admin website.
export const plusJakarta = Plus_Jakarta_Sans({
  variable: "--font-plus-jakarta",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
});

export const syne = Syne({
  variable: "--font-syne",
  subsets: ["latin"],
  weight: ["700", "800"],
});
