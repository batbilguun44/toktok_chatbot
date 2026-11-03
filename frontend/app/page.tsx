import { redirect } from "next/navigation";

// Entry point: the full-page chat demo. The popup demo (as TokTok's sites will show it)
// stays at /widget-demo.html.
export default function Home() {
  redirect("/demo");
}
