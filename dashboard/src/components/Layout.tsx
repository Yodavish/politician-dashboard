import { Landmark } from "lucide-react";
import { Link, NavLink } from "react-router-dom";
import { cn } from "@/lib/utils";

const navItems = [
  { to: "/", label: "Overview" },
  { to: "/transactions", label: "Recent trades", compactLabel: "Trades" },
  { to: "/politicians", label: "Politicians" },
  { to: "/signals", label: "Signals" },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <a
        href="#main-content"
        className="sr-only z-50 rounded-md bg-card px-4 py-2 text-foreground shadow focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
      >
        Skip to main content
      </a>
      <header className="sticky top-0 z-20 border-b border-border/90 bg-card/95 shadow-[0_1px_3px_rgb(23_53_79/4%)] backdrop-blur-sm">
        <div className="mx-auto flex w-full max-w-[1440px] flex-wrap items-center justify-between gap-x-8 gap-y-3 px-4 py-3 sm:px-6 lg:px-8">
          <Link to="/" className="group flex min-w-0 items-center gap-3 rounded-sm">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm">
              <Landmark className="size-5" aria-hidden="true" />
            </span>
            <span className="min-w-0">
              <span className="block text-[0.9375rem] font-semibold tracking-tight text-foreground">
                Politician Dashboard
              </span>
              <span className="hidden text-xs text-muted-foreground sm:block">
                Congressional trade disclosures
              </span>
            </span>
          </Link>

          <nav
            aria-label="Main navigation"
            className="flex w-full flex-wrap gap-1 sm:w-auto"
          >
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  cn(
                    "flex min-h-11 flex-1 items-center justify-center rounded-lg px-1.5 text-xs font-medium transition-colors sm:flex-none sm:px-3.5 sm:text-sm",
                    isActive
                      ? "bg-secondary text-primary"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )
                }
              >
                <>
                  <span className="sm:hidden">{item.compactLabel ?? item.label}</span>
                  <span className="hidden sm:inline">{item.label}</span>
                </>
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main
        id="main-content"
        className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-7 sm:px-6 sm:py-9 lg:px-8"
      >
        {children}
      </main>
    </div>
  );
}
