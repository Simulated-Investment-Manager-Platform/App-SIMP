import { render, screen } from "@testing-library/react";

import { CostCell, DataLabel, Side, Status } from "@/components/ui";

describe("ui", () => {
  it("status ships an icon and a label, never colour alone", () => {
    render(<Status value="FAILED" />);
    expect(screen.getByText("FAILED")).toBeInTheDocument();
  });
  it("labels synthetic data", () => {
    render(<DataLabel label="SYNTHETIC" />);
    expect(screen.getByText(/synthetic data/i)).toBeInTheDocument();
  });
  it("shows side as text", () => {
    render(<Side side="SELL" />);
    expect(screen.getByText("SELL")).toBeInTheDocument();
  });
  it("renders signed bps in cost cells", () => {
    render(<CostCell v={12.34} />);
    expect(screen.getByText(/12\.3/)).toBeInTheDocument();
  });
});
