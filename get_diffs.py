import math

sma_period = 10
prices = [100.0 + 10.0 * math.sin(i * 0.2) for i in range(500)]
smas = []
for i in range(500):
    if i < sma_period - 1:
        smas.append(None)
    else:
        smas.append(sum(prices[i-sma_period+1:i+1]) / sma_period)

diff_count_buy_02 = 0
diff_count_buy_10 = 0

diff_count_sell_02 = 0
diff_count_sell_10 = 0

for i in range(sma_period, 500):
    price = prices[i]
    sma = smas[i]

    t1 = 0.02
    t2 = 0.10

    buy_cond_02 = price > sma*(1-t1)
    buy_cond_10 = price > sma*(1-t2)

    sell_cond_02 = price < sma*(1+t1)
    sell_cond_10 = price < sma*(1+t2)

    if buy_cond_02: diff_count_buy_02 += 1
    if buy_cond_10: diff_count_buy_10 += 1

    if sell_cond_02: diff_count_sell_02 += 1
    if sell_cond_10: diff_count_sell_10 += 1

print(f"Buy 0.02: {diff_count_buy_02}")
print(f"Buy 0.10: {diff_count_buy_10}")

print(f"Sell 0.02: {diff_count_sell_02}")
print(f"Sell 0.10: {diff_count_sell_10}")
