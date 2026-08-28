/**
 * `/` — WHERE A JUDGE ARRIVES.
 *
 * There is no home screen, and there should not be: this product has one obvious
 * first question ("what have my runs done?") and a landing page whose only content
 * is a link to the answer is a page that wastes a click and a screenful.
 *
 * Without this file `/` is a 404. That matters more than it sounds: the first
 * thing anyone types is the bare host, and a 404 there reads as a broken
 * deployment regardless of how well every other screen works.
 *
 * `redirect` and not `notFound` or a rendered link, because the destination is not
 * conditional -- there is exactly one place to go. A signed-out visitor is sent on
 * to `/signin` by the runs screen itself, which is where that decision belongs:
 * putting it here would mean two places decide what "signed in" implies, and they
 * would drift.
 */

import { redirect } from "next/navigation";

export default function Home() {
  redirect("/runs");
}
