import { CommandDeck } from "@/components/desk/CommandDeck";
// R3-1 panels (multi-asset monitor + Alpaca paper execution)
import { MultiAssetPanel } from "@/components/desk/MultiAssetPanel";
import { AlpacaPanel } from "@/components/desk/AlpacaPanel";

export default function Home() {
  return (
    <>
      <CommandDeck />
      <div className="mx-auto w-full max-w-[1600px] space-y-5 px-4 py-5 sm:px-6">
        {/* R3-1 Row 1: multi-asset live monitor (8 instruments + correlation) */}
        <MultiAssetPanel />
        {/* R3-1 Row 2: Alpaca paper execution (balance + positions + orders) */}
        <AlpacaPanel />
      </div>
    </>
  );
}
