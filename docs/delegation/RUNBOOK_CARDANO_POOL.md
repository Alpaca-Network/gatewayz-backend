# Runbook: the Gatewayz Cardano stake pool

After this runbook, a Gatewayz stake pool (1 block producer + 2 relays) is
registered on Cardano mainnet with a ~99% margin, its cold keys have never
touched an online machine, and the backend is reading it through Koios. Users
then delegate ADA from their own wallets through `/rewards` (see
[README.md](README.md)).

**Who runs it:** a human engineer. Agents never hold keys, sign or submit
transactions, or set production env vars. On-chain steps are marked **[SIGN]**;
steps on the offline machine are marked **[AIR-GAP]**.

**Sources (all opened 2026-10-08):**

| Ref | Source |
|---|---|
| [dp] | Cardano Developer Portal, `cardano-foundation/developer-portal` @ `057f07c` (2026-10-06), `docs/operators/**` — pages say "written in May 2026 with reference to cardano-node and cardano-cli v11" (install page: release **11.0.1**). Rendered at https://developers.cardano.org/docs/operators/ |
| [cc] | CoinCashew, https://www.coincashew.com/coins/overview-ada/guide-how-to-build-a-haskell-stakepool-node/part-iii-operation/registering-your-stake-pool |
| [deleg] | https://cardano.org/stake-pool-delegation/ |
| [koios] | Koios API `https://api.koios.rest/api/v1` and its OpenAPI spec `https://api.koios.rest/koiosapi.yaml` |
| [genesis] | https://book.world.dev.cardano.org/environments/mainnet/shelley-genesis.json |
| [rel] | https://github.com/IntersectMBO/cardano-node/releases — latest stable **11.1.3** (2026-09-29) |

Live mainnet values read from Koios on 2026-10-08 (epoch 660, era Conway,
protocol 11.0): `min_pool_cost` = 170 000 000 lovelace (**170 ADA**),
`pool_deposit` = 500 000 000 (**500 ADA**), `key_deposit` = 2 000 000 (**2 ADA**).
From [genesis]: `slotsPerKESPeriod` 129 600, `maxKESEvolutions` 62, `epochLength`
432 000 slots (5 days).

Use cardano-node/cli **11.1.3** (or the latest stable on [rel]); the commands below
are copied from [dp], which targets v11. Run every command with
`CARDANO_NODE_NETWORK_ID=mainnet` and `CARDANO_NODE_SOCKET_PATH` set, as [dp]
assumes ("Setting `CARDANO_NODE_NETWORK_ID` removes the need to pass `--mainnet`").
Rehearse everything on **preprod** first [dp].

---

## 1. Decisions

### 1.1 Hosting (recommended)

| Machine | Spec floor [dp] | Recommendation |
|---|---|---|
| Block producer | x86, ≥2 cores ≥2 GHz; **24 GB RAM** (InMemory backend); 250 GB disk (350 GB recommended); public IPv4; ~1 GB/hour bandwidth | Dedicated bare-metal server, e.g. Hetzner AX42 (Ryzen 7 PRO 8700GE, 64 GB DDR5 ECC, NVMe), DC **FSN1** |
| Relay 1 | same | Hetzner AX42, DC **HEL1** |
| Relay 2 | same | A **second provider / region** so one provider outage does not take both relays |
| Air-gapped machine | any laptop that never connects to a network | Dedicated laptop booting the IntersectMBO **cardano-airgap** ISO [dp `security/air-gap.md`] |

Why bare metal: [dp] lists 1 block producer + at least 2 relays as the minimum;
dedicated servers give predictable RAM/disk for a node that must not fall
behind. Specs and data centers are from hetzner.com (2026-10-08); **the page did
not render prices**, so get a quote at order time and keep the total inside the
approved ~$1k/month (3 servers + monitoring).

### 1.2 Pool parameters

| Parameter | Value | Notes |
|---|---|---|
| Ticker | `GWAYZ` | Unused on mainnet per Koios `pool_list?ticker=eq.GWAYZ` (2026-10-08). 3–5 chars A–Z/0–9 satisfies both [cc] (3–5) and [dp] (3–9). Tickers are not unique on-chain. |
| Margin | **`0.99`** (recommended) or `1` | See §1.3 |
| Fixed cost | `170000000` lovelace (170 ADA) | Must be ≥ `minPoolCost` [dp]. Taken first, before margin. |
| Pledge | `<PLEDGE_LOVELACE>` — what the company will keep in the owner stake address | Must be met at every snapshot: "Failing to fulfill pledge will result in missed block minting opportunities and your delegators would miss rewards" [cc]. |
| Reward + owner account | the pool's `stake.vkey` | Pool fees (our revenue) are paid here. |
| Relays | `relay1.pool.gatewayz.ai:3001`, `relay2.pool.gatewayz.ai:3001` | DNS A records, `--single-host-pool-relay` [dp] |
| Metadata URL | `https://<HOST>/gwayz.json`, **≤ 64 characters**, HTTPS, **no redirects** [dp] | A static host we control. Do not rely on a path under www.gatewayz.ai (a different site that answers 200 on unknown paths); verify the served bytes with the hash check in §4. |

### 1.3 Margin 1.0 vs 0.99

Pool fees = fixed cost + margin × (pool rewards − fixed cost); delegators share
the rest.

- **`1` (100%)**: every lovelace above the fixed cost is ours; delegators' member
  rewards are exactly zero. Wallets that rank pools by expected delegator
  rewards put such pools last, and community reports say Daedalus hides
  100%-margin pools (we could not open the IOHK support article — HTTP 403 — so
  treat that as unverified). Some users will read "100%" in their wallet as a
  scam signal at signing time.
- **`0.99`**: delegators keep 1% of the variable part, so wallets show a small,
  non-zero return; we give up 1% of revenue. Matches the 99% ETH vault fee and the
  "99% off" framing.

Our users do not need to find the pool in a wallet's list — `CardanoPoolPanel`
delegates to `CARDANO_POOL_ID` directly — so discovery is not the deciding
factor; how the pool looks in the user's wallet is. **Recommend 0.99.** The
margin can be changed later with an updated registration certificate.

### 1.4 Economics you must plan for

Stake is rewarded per block. Koios epoch 659: 21 207 blocks over 21.32 B ADA
active stake ⇒ **≈ 1 block per epoch per 1 M ADA**. A pool with much less than
1 M ADA active stake will mint **no block in most epochs**, so `pool_fees` (our
revenue) will be zero for long stretches and reconciliation will pause ADA.
Before activating the ADA allowance rate, get active stake toward ~1 M ADA
(pledge plus company/partner delegation). Network-wide, epochs 655–657 paid
≈ 2.10 %/year of active stake in total rewards (Koios `epoch_info`), the
basis for the ~1.8 %/year net-to-us figure in LAUNCH_CHECKLIST.md.

---

## 2. Build the nodes

1. Install cardano-node/cli on all three servers from the release tarball [dp]:
   ```bash
   VERSION=11.1.3  # check https://github.com/IntersectMBO/cardano-node/releases
   wget https://github.com/IntersectMBO/cardano-node/releases/download/${VERSION}/cardano-node-${VERSION}-linux.tar.gz
   tar -xzf cardano-node-${VERSION}-linux.tar.gz -C ~/.local/
   ```
2. Download mainnet config files into `/etc/cardano/` [dp `node/running-cardano.md`]:
   ```bash
   curl -O -J "https://book.play.dev.cardano.org/environments/mainnet/{config,db-sync-config,submit-api-config,topology,byron-genesis,shelley-genesis,alonzo-genesis,conway-genesis,checkpoints}.json"
   ```
   Bootstrap the database with Mithril (genesis sync takes over 24 hours) [dp].
3. Run each node under systemd as user `cardano` [dp]. Relays listen on `3001`; the
   block producer on `6000`.
4. **Relay topology** (`/etc/cardano/topology.json`) — copy [dp]
   `relay-configuration/relay-node-configuration.md`: block producer as a
   `localRoots` entry with `"advertise": false`, the three IOG/Emurgo/CF
   `bootstrapPeers`, and `useLedgerAfterSlot` **taken from the official mainnet
   `topology.json`** (never `-1` on a relay).
5. **Block producer topology** — only your two relays in `localRoots`,
   `"bootstrapPeers": null`, `"publicRoots": []`, `"useLedgerAfterSlot": -1` [dp
   `block-producer/deployment.md`]. Firewall: port 6000 open **only** to the two
   relay IPs; SSH via VPN/bastion only.
6. Wait for `cardano-cli query tip` to show `"syncProgress": "100.00"` on all three.

---

## 3. Key ceremony (cold keys never online)

Prepare: boot the air-gapped laptop from the verified cardano-airgap ISO; create
two LUKS-encrypted key USBs (one stays offsite) with a passphrase never typed on
an online machine [dp `security/air-gap.md`]. Only unsigned tx bodies and public
files go *in*; only signed txs, `.vkey`/`.addr` files, `node.cert`, `vrf.skey`
and `kes.skey` come *out*.

1. **[AIR-GAP]** Payment + stake keys. For mainnet [dp] recommends
   cardano-addresses with a GPG-encrypted mnemonic on the air-gapped machine, or a
   hardware wallet via cardano-hw-cli. The raw CLI form [dp]:
   ```bash
   cardano-cli address key-gen --verification-key-file payment.vkey --signing-key-file payment.skey
   cardano-cli stake-address key-gen --verification-key-file stake.vkey --signing-key-file stake.skey
   cardano-cli stake-address build --stake-verification-key-file stake.vkey --out-file stake.addr
   cardano-cli address build --payment-verification-key-file payment.vkey \
     --stake-verification-key-file stake.vkey --out-file payment.addr
   ```
2. **[AIR-GAP]** Cold, KES and VRF keys [dp `block-producer-keys.md`]:
   ```bash
   cardano-cli node key-gen --cold-verification-key-file cold.vkey \
     --cold-signing-key-file cold.skey --operational-certificate-issue-counter cold.counter
   cardano-cli node key-gen-KES --verification-key-file kes.vkey --signing-key-file kes.skey
   cardano-cli node key-gen-VRF --verification-key-file vrf.vkey --signing-key-file vrf.skey
   ```
3. **Online (a relay)** — current KES period [dp]:
   ```bash
   slotsPerKESPeriod=$(jq -r '.slotsPerKESPeriod' /etc/cardano/shelley-genesis.json)
   currentSlot=$(cardano-cli query tip | jq -r '.slot')
   echo $(( currentSlot / slotsPerKESPeriod ))
   ```
4. **[AIR-GAP]** Operational certificate [dp `deployment.md`]:
   ```bash
   cardano-cli node issue-op-cert --kes-verification-key-file kes.vkey \
     --cold-signing-key-file cold.skey --operational-certificate-issue-counter cold.counter \
     --kes-period <KES_PERIOD> --out-file node.cert
   ```
   Record `<KES_PERIOD>` and the date in the KES calendar (§6).
5. Move `node.cert`, `vrf.skey`, `kes.skey` to the block producer, encrypted (age or
   encrypted USB) [dp], into `/run/secrets/`, `chmod 400` the keys, add
   `--shelley-kes-key`, `--shelley-vrf-key`, `--shelley-operational-certificate` to
   `ExecStart`, restart. `cold.skey` and `cold.counter` **never leave** the key USB.
6. Fund `payment.addr` from the company wallet **[SIGN]** with: pledge + 500 ADA pool
   deposit + 2 ADA stake-key deposit + ~5 ADA for fees.

---

## 4. Metadata, certificates, registration

1. Metadata file (fields per [dp]; description ≤ 255 chars):
   ```bash
   cat > gwayz.json << EOF
   {
     "name": "Gatewayz",
     "description": "<POOL_DESCRIPTION>",
     "ticker": "GWAYZ",
     "homepage": "https://gatewayz.ai"
   }
   EOF
   cardano-cli stake-pool metadata-hash --pool-metadata-file gwayz.json --out-file poolMetaDataHash.txt
   ```
   Host it at `<METADATA_URL>`, then verify the served bytes [dp]:
   ```bash
   cardano-cli stake-pool metadata-hash --pool-metadata-file <(curl -s -L <METADATA_URL>)
   cat poolMetaDataHash.txt   # must be identical
   ```
   Never edit the hosted file afterwards without re-registering with the new hash.
2. **Online** — register the stake address [dp `register-stake-address.md`]. Build on a relay:
   ```bash
   cardano-cli conway stake-address registration-certificate \
     --stake-verification-key-file stake.vkey --key-reg-deposit-amt 2000000 --out-file stake.cert
   currentSlot=$(cardano-cli query tip | jq -r '.slot')
   cardano-cli conway transaction build \
     --tx-in $(cardano-cli query utxo --address $(cat payment.addr) --out-file /dev/stdout | jq -r 'keys[0]') \
     --change-address $(cat payment.addr) --certificate-file stake.cert \
     --invalid-hereafter $(( currentSlot + 1000 )) --witness-override 2 --out-file tx.raw
   ```
   The Conway form needs the deposit: cardano-cli's CHANGELOG says "Make
   --key-reg-deposit-amt mandatory in the parser of conway stake-address
   registration-certificate", and its test
   `Test/Cli/Compatible/StakeAddress/RegistrationCertificate.hs` uses exactly this
   shape ([IntersectMBO/cardano-cli](https://github.com/IntersectMBO/cardano-cli),
   main, 2026-10-08). [dp] shows the era-less `cardano-cli stake-address
   registration-certificate` without it. 2000000 = `key_deposit` (2 ADA). **[AIR-GAP]** sign:
   ```bash
   cardano-cli conway transaction sign --tx-body-file tx.raw \
     --signing-key-file payment.skey --signing-key-file stake.skey --out-file tx.signed
   ```
   **[SIGN]** online: `cardano-cli conway transaction submit --tx-file tx.signed`.
3. **Online** — `cardano-cli query protocol-parameters --out-file protocol.json`;
   check `jq .minPoolCost protocol.json` is `170000000`. Carry `protocol.json`,
   `vrf.vkey`, `stake.vkey`, `poolMetaDataHash.txt` to the air-gapped machine.
4. **[AIR-GAP]** Pool registration + owner delegation certificates [dp `register-stake-pool.md`]:
   ```bash
   cardano-cli stake-pool registration-certificate \
     --cold-verification-key-file cold.vkey --vrf-verification-key-file vrf.vkey \
     --pool-pledge <PLEDGE_LOVELACE> --pool-cost 170000000 --pool-margin 0.99 \
     --pool-reward-account-verification-key-file stake.vkey \
     --pool-owner-stake-verification-key-file stake.vkey \
     --single-host-pool-relay relay1.pool.gatewayz.ai --pool-relay-port 3001 \
     --single-host-pool-relay relay2.pool.gatewayz.ai --pool-relay-port 3001 \
     --metadata-url <METADATA_URL> --metadata-hash $(cat poolMetaDataHash.txt) \
     --out-file pool.cert
   cardano-cli latest stake-address stake-delegation-certificate \
     --stake-verification-key-file stake.vkey --cold-verification-key-file cold.vkey --out-file deleg.cert
   ```
   [cc] shows the same command with `--mainnet`; pass it if your build does not
   pick up `CARDANO_NODE_NETWORK_ID`. The delegation certificate is what delegates
   the owner (pledge) stake to the pool.
5. **Online** — build with both certificates (the 500 ADA deposit is added by `build`) [dp]:
   ```bash
   currentSlot=$(cardano-cli query tip | jq -r '.slot')
   cardano-cli conway transaction build \
     --tx-in $(cardano-cli query utxo --address $(cat payment.addr) --out-file /dev/stdout | jq -r 'keys[0]') \
     --change-address $(cat payment.addr) \
     --certificate-file pool.cert --certificate-file deleg.cert \
     --invalid-hereafter $(( currentSlot + 1000 )) --witness-override 3 --out-file tx.raw
   ```
6. **[AIR-GAP]** sign with payment, cold and stake keys **on the air-gapped
   machine** (the cold key is never brought online; [dp] allows signing on a
   separate machine):
   ```bash
   cardano-cli conway transaction sign --tx-body-file tx.raw \
     --signing-key-file payment.skey --signing-key-file cold.skey --signing-key-file stake.skey \
     --out-file tx.signed
   ```
   **[SIGN]** online: `cardano-cli conway transaction submit --tx-file tx.signed`.
   `--invalid-hereafter` gives ~1000 s, so carry the body across promptly or
   rebuild with a later slot.
7. Pool id (hex) [dp], then its bech32 form via Koios [koios]:
   ```bash
   cardano-cli stake-pool id --cold-verification-key-file cold.vkey --output-format hex > stakepoolid.txt
   curl -s "https://api.koios.rest/api/v1/pool_list?pool_id_hex=eq.$(cat stakepoolid.txt)&select=pool_id_bech32,ticker"
   ```
   Record `pool1…` as `<POOL_ID>`.

---

## 5. Timeline

From [deleg]: "If you delegate in epoch N, your stake is counted in the snapshot
at the start of epoch N+1, becomes active in epoch N+2, the rewards for that
epoch are calculated during N+3 and paid at the start of N+4" — first rewards
"About 15 to 20 days after you delegate, provided the pool mints blocks." One
epoch = 5 days.

| When | Pool / chain | Gatewayz backend |
|---|---|---|
| Epoch N | Registration + owner delegation submitted | — |
| N+1 boundary | Pool visible in explorers / Koios `pool_info` | Set `CARDANO_POOL_ID` (§8) |
| N+2 (~5–10 days) | Pledge/delegations **active**; pool can be elected to mint | ADA measurements count a delegator once `active_epoch_no <= tip` |
| N+4 (~15–20 days) | First rewards paid (if a block was minted in N+2) | Reconciliation records `pool_fees` for an epoch only when it is ≤ tip − 2, so epoch N+2's fees land at N+4 |

So ADA credits start ~2 epochs before the matching revenue is recorded — size
the grace in §8 for that.

---

## 6. KES rotation calendar

An op cert issued at KES period P is valid for 62 periods × 129 600 slots =
8 035 200 s ≈ **93 days** [genesis]; "When they expire the node stops minting
blocks" [dp].

| Day | Action |
|---|---|
| 0 | Op cert issued at `<KES_PERIOD>`; record expiry = issue date + 93 days |
| 60 | **Rotate** (planned) — 33 days of buffer |
| 79 | Alert if not rotated (14 days left) |
| 93 | Expiry — node stops minting |

Rotation [dp]: **[AIR-GAP]** `node key-gen-KES` → online KES period →
**[AIR-GAP]** `node issue-op-cert` (counter increments automatically; never copy
an old `cold.counter` back) → copy `node.cert` + `kes.skey` to the block
producer → `pkill -HUP cardano-node`. Then confirm Koios `pool_info.op_cert_counter`
increases after the next block.

---

## 7. Monitoring

Run cardano-tracer → Prometheus → Grafana → Alertmanager [dp
`monitoring/monitoring-overview.md`]; the IOG dashboard's **Forging** row shows
"Leader slots, blocks forged, KES periods remaining, missed slots". Alert on:

| Signal | Threshold |
|---|---|
| Node sync / slot height | any node > 2 min behind tip |
| Process liveness | any node down > 2 min |
| KES periods remaining | < 10 (≈ 15 days) |
| Hot peers | relay < 5, block producer < 2 (its relays) |
| Disk free | < 20% |
| Missed leader slots | any |
| Live pledge (Koios `pool_info.live_pledge`) | < declared `pledge` |

Gatewayz's own alerts (`delegation_measured_nothing_ada`,
`delegation_revenue_read_failed_ada`, `delegation_overspent_ada`) go to the ops
email and Sentry; see LAUNCH_CHECKLIST.md.

---

## 8. Configure Gatewayz and verify

Railway → service **`api`** → Variables:

| Var | Value |
|---|---|
| `CARDANO_POOL_ID` | `<POOL_ID>` — bech32 `pool1…`; anything else reads as unset |
| `KOIOS_API_KEY` | Bearer token from the koios.rest Profile page (the spec: "JWT Bearer Auth token generated via https://koios.rest Profile page"). A secret: never logged, sent only as `Authorization: Bearer`. The public tier works without it but has low limits. |
| `KOIOS_BASE_URL` | leave default `https://api.koios.rest/api/v1` |
| `DELEGATION_ALLOW_CARDANO_TESTNET` | `false` (never on in prod) |
| `DELEGATION_RECONCILIATION_GRACE_USD` | formula below |

### Grace formula

Reconciliation pauses an asset when
`credits_granted × (1 − m) > revenue × (1 + tolerance) + grace`
(`src/services/delegation/reconciliation.py`). ADA revenue lags credits by up to
**3 epochs = 15 days** (§5), so:

```
grace_usd = (1 − m) × max( 15 × C_ada , L_eth × C_eth )
C_asset   = min( Σ delegated_usd / 1000 × rate_asset ,  DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS )
L_eth     = days from the first ETH deposit to the first fee mint (activation queue + 1)
```

`m` = `DELEGATION_INFERENCE_MARGIN` (0.20); the single grace applies to each asset
separately, hence the `max`. **Ceiling at the default 50-credit budget:
0.8 × 15 × 50 = $600.** Example: $50 000 of ADA delegated at a rate of 0.0616
credits/$1k/day → C_ada = 3.08 → grace = 0.8 × 15 × 3.08 ≈ **$37**. Revisit after
~6 epochs of revenue and lower it; it is a permanent absolute allowance, not a
one-off.

### Verify

```bash
# On-chain view (Koios, no key needed for one call)
curl -s -X POST https://api.koios.rest/api/v1/pool_info -H 'content-type: application/json' \
  -d '{"_pool_bech32_ids":["<POOL_ID>"]}' \
  | jq '.[0] | {pool_status, margin, fixed_cost, pledge, live_pledge, active_stake, live_stake, block_count, meta_url, meta_hash}'
# expect pool_status "registered", margin 0.99, fixed_cost "170000000", live_pledge >= pledge

curl -s "https://api.koios.rest/api/v1/pool_history?_pool_bech32=<POOL_ID>" \
  | jq '.[:3] | map({epoch_no, block_cnt, active_stake, pool_fees})'   # pool_fees = our revenue

# Gatewayz
curl -s https://api.gatewayz.ai/delegation/status | jq '.data.cardano'      # {"pool_id": "<POOL_ID>"}
curl -s -H "Authorization: Bearer <SUPERADMIN_API_KEY>" \
  https://api.gatewayz.ai/admin/delegation/reconciliation | jq '.data.ada'  # configured: true
```

---

## 9. Never do

- Never let `cold.skey` or `cold.counter` touch an internet-connected machine; "If your cold key is compromised, an attacker can re-register your pool to their reward address" [dp].
- Never copy an old `cold.counter` back [dp].
- Never let the owner address fall below the pledge at a snapshot.
- Never change the hosted metadata file without re-registering its hash.
- Never expose the block producer's IP (`advertise: false`, firewall to relays only).
- Never run two block producers with the same keys at once.
- Never link the owner/reward stake address to a Gatewayz account.
- Never paste keys, mnemonics or the Koios token into tickets, chats, agent sessions or vault notes.
