import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Loudrr | Engage & Earn",
  description: "Engage with the community, earn karma points",
};

export default function MiniAppLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return <>{children}</>;
}
