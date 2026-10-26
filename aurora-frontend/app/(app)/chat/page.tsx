import { redirect } from "next/navigation";

/** The spec names this route chat/page.tsx; the assistant lives at /assistant, so /chat keeps old links working. */
export default function ChatPage() {
  redirect("/assistant");
}
