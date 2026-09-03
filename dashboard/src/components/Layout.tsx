import { Link, NavLink } from "react-router-dom";
import { cn } from "@/lib/utils";
import HealthBadge from "@/components/HealthBadge";

const navItems = [
  { to: "/transactions", label: "Recent Trades" },
  { to: "/politicians", label: "Politicians" },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-background text-foreground flex min-h-screen flex-col">
      <header className="bg-primary text-primary-foreground sticky top-0 z-10">
        <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
          <Link to="/transactions" className="text-base font-bold">
            Politician Dashboard
          </Link>
          <nav className="flex items-center gap-1">
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                    isActive
                      ? "bg-primary-foreground text-primary"
                      : "text-primary-foreground/80 hover:bg-primary-foreground/10 hover:text-primary-foreground",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <HealthBadge />
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6">
        {children}
      </main>
    </div>
  );
}
