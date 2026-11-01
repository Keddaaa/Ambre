import { Suspense } from "react";

import { MarketDetail } from "@/components/market-detail";
import { LoadingState } from "@/components/state";

async function Resolved({ params }: Pick<PageProps<"/markets/[id]">, "params">) {
  const { id } = await params;
  return <MarketDetail id={id} />;
}

export default function MarketDetailPage(props: PageProps<"/markets/[id]">) {
  return (
    <Suspense fallback={<LoadingState rows={6} />}>
      <Resolved params={props.params} />
    </Suspense>
  );
}
