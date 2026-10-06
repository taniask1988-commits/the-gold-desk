# BAR-OPT-MATH — the published worked example (Hull, Options, Futures and Other Derivatives)

John C. Hull, *Options, Futures, and Other Derivatives* (classic worked example, appears in
Ch. 15 "The Black-Scholes-Merton Model" of multiple editions):

- S = 42 (spot)
- K = 40 (strike)
- r = 0.10 (risk-free, continuously compounded)
- sigma = 0.20 (volatility)
- T = 0.5 (years)

Closed-form inputs:
  d1 = (ln(S/K) + (r + sigma^2/2) T) / (sigma sqrt(T))
     = (ln(1.05) + (0.10 + 0.02) * 0.5) / (0.20 * sqrt(0.5))
     = (0.048790164 + 0.06) / 0.141421356
     = 0.769291501
  d2 = d1 - sigma sqrt(T) = 0.627841... (0.769291501 - 0.141421356 = 0.627870145)

Reference values (Hull):
  European call  c = 4.76   (Hull prints 4.76)
  European put   p = 0.81   (Hull prints 0.81)

Standard Greeks for this contract (independent closed-form oracle):
  delta_call = N(d1)  = 0.7791...
  delta_put  = N(d1)-1 = -0.2209...
  gamma = phi(d1) / (S sigma sqrt(T)) = 0.0654...
  vega  = S phi(d1) sqrt(T) = 11.62...   (per 1.00 vol; 0.1162 per 1 vol point)
  theta_call (per year) = -S phi(d1) sigma / (2 sqrt(T)) - r K e^{-rT} N(d2) = -6.6... (per year)
  rho_call = K T e^{-rT} N(d2) = 12.05...

CERTIFICATION RULE (BAR-OPT-MATH): our implementation must reproduce
c = 4.76 and p = 0.81 (Hull's published rounding) and match the closed-form
Greeks to < 1e-8 relative tolerance across a parameter grid vs the analytic
identities (put-call parity |c - p - (S - K e^{-rT})| < 1e-8, delta call-put
difference = 1, and numerical-derivative cross-checks with Richardson-grade
step choice).
