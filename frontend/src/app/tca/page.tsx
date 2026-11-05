"use client";

import { RunScope } from "@/components/RunPicker";
import { TcaPanel } from "@/components/TcaPanel";

export default function TcaPage() {
  return (
    <RunScope title="Transaction cost analysis" subtitle="Where each dollar of cost came from, and how it scales with size against liquidity. Every figure is notional-weighted.">
      {(runId) => <TcaPanel runId={runId} />}
    </RunScope>
  );
}
