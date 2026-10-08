-- Migration: register liquid ETH tokens in holdings_tokens
-- (gatewayz-backend holdings rewards -- see
-- 20260915000000_holdings_rewards.sql and docs/holdings/REWARDS.md.)
-- Created: 2026-10-08
--
-- Ten ERC-20 rows: stETH, wstETH, rETH and cbETH on Ethereum, and wstETH,
-- rETH and cbETH on Base and Arbitrum One. Each is valued at its OWN
-- CoinGecko price id, never at ETH's: wstETH, rETH and cbETH are
-- non-rebasing and trade at a growing premium to ETH, so pricing them as
-- ETH would undervalue every holder. stETH rebases, which balanceOf
-- already reflects.
--
-- The registry is the trust root (whoever controls it controls what is
-- valued), so every address below was verified on 2026-10-08:
--   * on-chain: symbol() and decimals() (= 18) via eth_call on each chain,
--     with a non-trivial totalSupply();
--   * L2 provenance: on Arbitrum the canonical L2GatewayRouter
--     (0x5288c571...4C84F933).calculateL2TokenAddress(<L1 token>) returns
--     exactly the address below for wstETH, rETH and cbETH; on Base, rETH
--     and cbETH are OptimismMintableERC20s whose remoteToken() is the L1
--     token and bridge() is the canonical L2StandardBridge (0x42...10);
--     wstETH on Base is Lido's custom-bridge token;
--   * docs: stETH and all three wstETH addresses match docs.lido.fi
--     /deployed-contracts; the rest match the token's CoinGecko listing and
--     block-explorer labels;
--   * price ids: staked-ether, wrapped-steth, rocket-pool-eth and
--     coinbase-wrapped-staked-eth all return a fresh USD price from
--     /simple/price.
--
-- Addresses are stored lowercase, matching src/db/holdings.py::create_token.
-- Idempotent and non-destructive: a row ops already added for the same
-- (chain, contract) -- in any letter case -- is left exactly as it is,
-- including its is_enabled flag, and a re-apply inserts nothing.
--
-- Version stamp 20261008120000 is newer than, and distinct from, every
-- stamp in supabase/migrations, supabase/migrations_backup,
-- supabase/staged-migrations and every open PR at the time of writing.

insert into public.holdings_tokens (chain_id, contract_address, symbol, decimals, price_id, is_enabled)
select v.chain_id, v.contract_address, v.symbol, v.decimals, v.price_id, true
from (
  values
    -- Ethereum (1)
    (1,     '0xae7ab96520de3a18e5e111b5eaab095312d7fe84', 'stETH',  18, 'staked-ether'),
    (1,     '0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0', 'wstETH', 18, 'wrapped-steth'),
    (1,     '0xae78736cd615f374d3085123a210448e74fc6393', 'rETH',   18, 'rocket-pool-eth'),
    (1,     '0xbe9895146f7af43049ca1c1ae358b0541ea49704', 'cbETH',  18, 'coinbase-wrapped-staked-eth'),
    -- Base (8453)
    (8453,  '0xc1cba3fcea344f92d9239c08c0568f6f2f0ee452', 'wstETH', 18, 'wrapped-steth'),
    (8453,  '0xb6fe221fe9eef5aba221c348ba20a1bf5e73624c', 'rETH',   18, 'rocket-pool-eth'),
    (8453,  '0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22', 'cbETH',  18, 'coinbase-wrapped-staked-eth'),
    -- Arbitrum One (42161)
    (42161, '0x5979d7b546e38e414f7e9822514be443a4800529', 'wstETH', 18, 'wrapped-steth'),
    (42161, '0xec70dcb4a1efa46b8f2d97c310c9c4790ba5ffa8', 'rETH',   18, 'rocket-pool-eth'),
    (42161, '0x1debd73e752beaf79865fd6446b0c970eae7732f', 'cbETH',  18, 'coinbase-wrapped-staked-eth')
) as v(chain_id, contract_address, symbol, decimals, price_id)
where not exists (
  select 1
  from public.holdings_tokens t
  where t.chain_id = v.chain_id
    and lower(t.contract_address) = v.contract_address
)
on conflict do nothing;
