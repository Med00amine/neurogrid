import { fireEvent, render, screen } from "@testing-library/react";
import { Columns, HBars, LineChart, Meter, StatTile } from "./charts";

describe("charts", () => {
  it("HBars draws one row per item with values, and an honest empty state", () => {
    const { rerender } = render(<HBars rows={[{ name: "Eligible", value: 12 }, { name: "Excluded", value: 3 }]} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("12")).toBeInTheDocument();
    rerender(<HBars rows={[{ name: "A", value: 0 }]} emptyText="Nothing yet" />);
    expect(screen.getByText("Nothing yet")).toBeInTheDocument();
  });

  it("Columns offers a table view with the same numbers (accessibility)", () => {
    render(<Columns data={[{ label: "0-9", value: 5 }, { label: "10-19", value: 0 }]} unit="contacts" />);
    expect(screen.getByRole("img")).toBeInTheDocument();
    fireEvent.click(screen.getByText("View as table"));
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("0-9")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("LineChart shows a legend for two series and can switch to a table", () => {
    render(<LineChart labels={["Nov 1", "Nov 2"]} series={[{ name: "Sent", color: "red", values: [1, 2] }, { name: "Replies", color: "blue", values: [0, 1] }]} />);
    expect(screen.getByText("Sent")).toBeInTheDocument();
    expect(screen.getByText("Replies")).toBeInTheDocument();
    fireEvent.click(screen.getByText("View as table"));
    expect(screen.getAllByRole("row")).toHaveLength(3);
  });

  it("Meter exposes its value to assistive technology and caps the bar at 100%", () => {
    render(<Meter label="AI spend" value={30} max={20} format={(n) => `$${n}`} />);
    const m = screen.getByRole("meter", { name: "AI spend" });
    expect(m).toHaveAttribute("aria-valuenow", "30");
    expect((m.firstElementChild as HTMLElement).style.width).toBe("100%");
  });

  it("StatTile labels estimates and simulated numbers but not plain database counts", () => {
    const { rerender } = render(<StatTile label="Spend" value="$1" basis="estimate" />);
    expect(screen.getByText("estimate")).toBeInTheDocument();
    rerender(<StatTile label="Contacts" value="4" basis="database" />);
    expect(screen.queryByText("database")).not.toBeInTheDocument();
  });
});
