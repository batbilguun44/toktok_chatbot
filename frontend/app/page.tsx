import { redirect } from "next/navigation";

// Local entry point: the popup demo as TokTok's sites will show it.
// The full-page chat demo moved to /demo.
export default function Home() {
  redirect("/widget-demo.html");
}
