import { AlertCircle, Inbox } from "lucide-react";
import { Alert, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="space-y-3 py-6" data-testid="loading">
      <Skeleton className="h-4 w-1/3" />
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-10 w-5/6" />
      <span className="text-muted-foreground block pt-1 text-sm">{label}</span>
    </div>
  );
}

export function ErrorMessage({ message }: { message: string }) {
  return (
    <Alert variant="destructive" className="my-4">
      <AlertCircle className="size-4" />
      <AlertTitle>Error</AlertTitle>
      <div className="text-muted-foreground col-start-2 text-sm">
        Error: {message}
      </div>
    </Alert>
  );
}

export function Empty({ message = "No results." }: { message?: string }) {
  return (
    <div
      className="text-muted-foreground flex flex-col items-center justify-center gap-2 py-10 text-center"
      data-testid="empty-state"
    >
      <Inbox className="text-muted-foreground/60 size-8" />
      <p className="text-sm">{message}</p>
    </div>
  );
}
