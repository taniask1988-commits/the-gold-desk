import type { Metadata } from "next";
import { ChatRoom } from "@/components/desk/ChatRoom";

export const metadata: Metadata = {
  title: "The Desk · Gold Desk Command",
  description:
    "Hermes-style trading agent window — chat with The Desk (20-yr gold veteran, free Zen models) grounded with live spot, Treasury curve, CFTC positioning, and your harness journal.",
};

export default function ChatPage() {
  return <ChatRoom />;
}
