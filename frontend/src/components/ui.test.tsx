import { fireEvent, render, screen } from "@testing-library/react";
import { Confirm, QueryState, StatusBadge, Basis } from "./ui";

describe("StatusBadge", () => {
  it("never relies on colour alone: it always has a text label, and an icon for good/warn/bad", () => {
    const { container } = render(<><StatusBadge status="eligible" /><StatusBadge status="review_required" /><StatusBadge status="bounced" /></>);
    expect(screen.getByText("Eligible")).toBeInTheDocument();
    expect(screen.getByText("Review required")).toBeInTheDocument();
    expect(screen.getByText("Bounced")).toBeInTheDocument();
    expect(container.querySelectorAll(".badge svg")).toHaveLength(3);
  });
  it("marks dry-run numbers as simulated", () => {
    render(<Basis basis="simulated" />);
    expect(screen.getByText("simulated")).toHaveAttribute("title", expect.stringContaining("Nothing was actually sent"));
  });
});

describe("QueryState", () => {
  const base = { isLoading: false, isError: false, error: null, data: { a: 1 } };
  it("shows a spinner while loading or while there is no data yet (never crashes on missing data)", () => {
    const { rerender } = render(<QueryState q={{ ...base, isLoading: true, data: undefined }}>content</QueryState>);
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
    rerender(<QueryState q={{ ...base, data: undefined }}>content</QueryState>);
    expect(screen.queryByText("content")).not.toBeInTheDocument();
  });
  it("shows the error message instead of content", () => {
    render(<QueryState q={{ ...base, isError: true, error: new Error("boom") }}>content</QueryState>);
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
  });
  it("renders children when data is present", () => {
    render(<QueryState q={base}>content</QueryState>);
    expect(screen.getByText("content")).toBeInTheDocument();
  });
});

describe("Confirm with a typed phrase", () => {
  it("keeps the confirm button disabled until the exact phrase is typed", () => {
    const onConfirm = vi.fn();
    render(<Confirm title="Send?" phrase="SEND 3 EMAILS" confirmLabel="Send" onConfirm={onConfirm} onCancel={() => {}}>body</Confirm>);
    const btn = screen.getByRole("button", { name: "Send" });
    expect(btn).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Confirmation phrase"), { target: { value: "send 3 emails" } });
    expect(btn).toBeDisabled(); // case matters
    fireEvent.change(screen.getByLabelText("Confirmation phrase"), { target: { value: "SEND 3 EMAILS" } });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
  it("closes on Escape", () => {
    const onCancel = vi.fn();
    render(<Confirm title="x" onConfirm={() => {}} onCancel={onCancel}>b</Confirm>);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });
});
