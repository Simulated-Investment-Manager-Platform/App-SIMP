"use client";

import { Blotter } from "@/components/Blotter";
import { RunScope } from "@/components/RunPicker";

export default function BlotterPage() {
  return (
    <RunScope title="Blotter" subtitle="PM order → trader orders → child orders → fills. Click a row for its lineage; expand to drill down.">
      {(runId) => <Blotter runId={runId} />}
    </RunScope>
  );
}
