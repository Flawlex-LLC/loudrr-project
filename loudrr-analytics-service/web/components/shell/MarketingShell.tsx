import { Header } from "../Header";
import { Footer } from "../Footer";

// Standalone marketing chrome for the public landing (no sidebar) — shares tokens/components
// with the app-shell but stays distraction-free and conversion-focused.
export function MarketingShell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <Header />
      <main>{children}</main>
      <Footer />
    </>
  );
}
