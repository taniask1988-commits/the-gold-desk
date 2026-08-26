import { CommandDeck } from "@/components/desk/CommandDeck";
// R3-1 panels (multi-asset monitor + Alpaca paper execution)
import { MultiAssetPanel } from "@/components/desk/MultiAssetPanel";
import { AlpacaPanel } from "@/components/desk/AlpacaPanel";
// R3-2 panels (news NLP sentiment + risk engine + backtest)
import { SentimentPanel } from "@/components/desk/SentimentPanel";
import { RiskPanel } from "@/components/desk/RiskPanel";
import { BacktestPanel } from "@/components/desk/BacktestPanel";

export default function Home() {
  return (
    <>
      <CommandDeck />
      <div className="mx-auto w-full max-w-[1600px] space-y-5 px-4 py-5 sm:px-6">
        {/* R3-1 Row 1: multi-asset live monitor (8 instruments + correlation) */}
        <MultiAssetPanel />
        {/* R3-1 Row 2: Alpaca paper execution (balance + positions + orders) */}
        <AlpacaPanel />
        {/* R3-2 Row 3: news NLP sentiment (polarity gauge + tape) */}
        <SentimentPanel />
        {/* R3-2 Row 4: risk engine (VaR/ES/beta/stress) */}
        <RiskPanel />
        {/* R3-2 Row 5: backtest (equity curve + stat grid) */}
        <BacktestPanel />
      </div>
    </>
  );
}
