import { CommandDeck } from "@/components/desk/CommandDeck";
// R3-1 panels (multi-asset monitor + Alpaca paper execution)
import { MultiAssetPanel } from "@/components/desk/MultiAssetPanel";
import { AlpacaPanel } from "@/components/desk/AlpacaPanel";
// R3-2 panels (news NLP sentiment + risk engine + backtest)
import { SentimentPanel } from "@/components/desk/SentimentPanel";
import { RiskPanel } from "@/components/desk/RiskPanel";
import { BacktestPanel } from "@/components/desk/BacktestPanel";
// R3-3 panels (portfolio construction + P&L attribution)
import { PortfolioPanel } from "@/components/desk/PortfolioPanel";
import { AttributionPanel } from "@/components/desk/AttributionPanel";
// R4-1 panels (autonomous watch loop + alert engine)
import { AlertsPanel } from "@/components/desk/AlertsPanel";

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
        {/* R3-3 Row 6: portfolio construction (MV/RP/HRP weights + risk contributions) */}
        <PortfolioPanel />
        {/* R3-3 Row 7: P&L attribution (by asset / by setup / by hour + sessions) */}
        <AttributionPanel />
        {/* R4-1 Row 8: autonomous watch loop (alert rules + fired feed + ack) */}
        <AlertsPanel />
      </div>
    </>
  );
}
