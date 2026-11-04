import { AcceptInviteForm } from "./AcceptInviteForm";

export const metadata = { title: "Accept invitation", referrer: "no-referrer" };

export default function AcceptInvitePage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-center gap-2">
          <span className="h-4 w-4 bg-primary" aria-hidden />
          <span className="text-xl font-semibold tracking-tight">Aurora</span>
        </div>
        <AcceptInviteForm />
      </div>
    </main>
  );
}
