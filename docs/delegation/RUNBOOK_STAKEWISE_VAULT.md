# Runbook: the Gatewayz StakeWise V3 ETH vault

After this runbook, a Gatewayz-owned StakeWise V3 vault is live on Ethereum
mainnet with a ~99% fee, at least one active validator, and the backend reading
it. Users then stake from their own wallets through `/rewards` and the vault fee
pays for their inference allowance (see [README.md](README.md)).

**Who runs it:** a human engineer, from company wallets. Agents never sign,
never hold keys, and never set production env vars. Every on-chain step below is
marked **[SIGN]**.

**Sources (all opened 2026-10-08):**

| Ref | Source |
|---|---|
| [core] | `stakewise/v3-core` @ `fc70cbe` (tag v5.0.1, 2026-06-25) — https://github.com/stakewise/v3-core |
| [deploy] | `v3-core/deployments/mainnet.json` @ `fc70cbe` |
| [create] | https://docs.stakewise.io/operator/create-regular-vault |
| [types] | https://docs.stakewise.io/docs/vaults/vault-types |
| [fees] | https://docs.stakewise.io/docs/fees/intro |
| [intro] | https://docs.stakewise.io/operator/introduction |
| [svc] | https://docs.stakewise.io/operator/launch-operator-service |
| [keys] | https://docs.stakewise.io/operator/validator-keys |
| [vm] | https://docs.stakewise.io/operator/validators-manager |
| [start] | https://docs.stakewise.io/operator/start-operator |
| [mon] | https://docs.stakewise.io/operator/operator-monitoring |
| [sp] | https://docs.stakewise.io/operator/smoothing-pool-relays |
| [nodes] | https://docs.stakewise.io/operator/staking-nodes |
| [spec] | `ethereum/consensus-specs` master, `specs/electra/beacon-chain.md` |

---

## 1. Decisions (make these before anything else)

| Decision | Recommendation | Why |
|---|---|---|
| Vault type | **Blocklist vault** (`EthBlocklistVault`, factory `BlocklistVaultFactory`) | Open to any wallet, but a Blocklist Manager can block addresses — the compliance lever counsel is likely to want [types]. Same `deposit(receiver, referrer)` / `getShares` interface our frontend and backend call ([core] `EthBlocklistVault.sol:56`). Shares are non-transferable. |
| Not this | ERC-20 vault | Transferable shares let users shuffle shares between linked wallets, and make it easy to move the fee recipient's shares by accident. |
| Not this | Private vault | Whitelist-only [types]; every user would need a whitelist transaction from us. |
| Not this | MetaVault | Runs no validators itself [types]; adds a layer we do not read. |
| Fee | **9900 bps (99.00%)** set **at creation** | See §1.1. 10000 (100%) is allowed; the backend and copy work with either. |
| Capacity | **Unlimited** | Capacity is fixed at creation ("only the Vault fee can be changed after creation" [create]). Throttle with `DELEGATION_DAILY_CAP_CREDITS` / `DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS` instead. |
| Block rewards (MEV) | **Smoothing Pool** | A small vault with one or two validators gets lumpy MEV; the pool smooths it [sp]. Permanent choice [create]. Requires DAO-approved relays and the pool address as fee recipient in the validator client [sp]. |
| Node operator | **Run the StakeWise Operator Service ourselves** | See §1.2. |

### 1.1 Why the fee must be set at creation

From [core] `contracts/vaults/modules/VaultFee.sol`:

- `_maxFeePercent = 10_000` (100.00%).
- At creation (`isVaultCreation = true`) any value up to 10 000 is accepted.
- Afterwards `setFeePercent` reverts with `TooEarlyUpdate` within **3 days** of the
  last change, and caps a new fee at `current × 120 / 100` — or at **100 bps (1%)
  when the current fee is 0**.

So a vault created at 0% needs 1% → 1.2% → 1.44% → … ≈ 26 steps × 3 days ≈
**11 weeks** to reach 99%. [fees] says the same: "fees can only be increased by
20% at a time", "a 3-day delay between updates", and from 0% "the initial
increase cannot exceed 1%". From 99% the vault *can* go to 100% in one step
later (9900 × 1.2 > 10 000), and decreases are not capped, so 99% at creation
keeps both options open.

The fee is minted as new vault shares to `feeRecipient` on each harvest
([core] `VaultState.sol:189-209`, event `FeeSharesMinted`). That share growth is
exactly what our reconciliation reads as ETH revenue.

### 1.2 Node operator: self-run vs partner

| | Self-run Operator Service (recommended) | Partner operator (e.g. Chorus One private vault) | Kiln stVault |
|---|---|---|---|
| What it is | Our own execution + consensus + validator client, plus StakeWise's open-source Operator Service, which "automatically handles Validator Registration, Validator Funding, and Withdrawals" [svc] | Partner runs the validators for a vault for us; Chorus One advertises "private, tailor-made vaults" ([Chorus One, 2023-11-28](https://chorus.one/articles/a-comprehensive-guide-to-stakewise-v3)) | Lido V3 vault, "Kiln operates the validators and you retain full administrative control" ([Kiln docs](https://docs.kiln.fi/v1/kiln-products/onchain/lido-v3-kiln-stvaults)) |
| Fits our code | Yes | Yes, if the vault is StakeWise V3 | **No** — not a StakeWise vault; `stakewise.py` and `eth-vault.ts` cannot read it. A separate integration. |
| Cost | One server (§3) + gas: "Each validator registration costs ~0.01 ETH at a 30 Gwei gas price" [vm] | Not published; Chorus One says to email staking@chorus.one. Kiln: "Negotiated per partner". [intro]: "your costs depend on your arrangement with the provider." Their cut comes out of our ~99%. | Lido fees 1% + 6.5% on minted stETH, plus Kiln's negotiated fee |
| Effort | 24/7 on-call, client upgrades, key custody, slashing protection | Contract + monitoring only | Contract + a new backend reader |
| Lead time | Days (sync + setup) | Weeks (sales cycle) | Weeks + engineering |

**Recommendation: self-run.** The same engineer already has to run the Cardano
pool (RUNBOOK_CARDANO_POOL.md), the vault will be small at launch, and a
partner's negotiated fee would eat into the fee that funds the allowance.
Switch to a partner if we cannot staff on-call, or once TVL makes slashing risk
material.

---

## 2. Wallets and roles

| Role | Holder | Set by | Notes |
|---|---|---|---|
| Deployer (temporary admin) | Hardware-wallet EOA, ~0.05 ETH | — | `createVault` makes `msg.sender` the admin, fee recipient and blocklist manager ([core] `EthVaultFactory.sol`, `EthVault.sol:147-148`, `EthBlocklistVault.sol`). Hand all roles off in §5. |
| **Vault admin** | Company **Safe**, ≥2-of-3, hardware-wallet signers | `setAdmin` | "Full Vault control". Changes fee, roles, metadata. |
| **Fee recipient** | Dedicated **Treasury Safe** used for nothing else | `setFeeRecipient` | Receives the fee as vault shares. **Never move these shares** (§9). |
| Validators manager | Operator hot wallet on the operator server, ~0.1 ETH for gas | Admin, UI → Settings → Roles [vm] | Authorizes validator registration/funding/withdrawals. |
| Blocklist manager | Vault admin Safe (or a compliance Safe) | `setBlocklistManager` | Blocks sanctioned/abusive addresses. |
| Validator key mnemonic | Paper/steel backup in the company safe | — | "It is the only way to recover your validator keys" [keys]. |

Addresses (from [deploy], Ethereum mainnet):

| Contract | Address |
|---|---|
| `BlocklistVaultFactory` | `0x608d8Ca6916b96edf63Dd429e62Fe1366ae6f3B5` |
| `VaultFactory` (Standard vault) | `0x7A8cbBf690084E43De778173cfAcf7313c9122DD` |
| `SharedMevEscrow` (Smoothing Pool) | `0x48319f97E5Da1233c21c48b80097c0FB7a20Ff86` (matches [sp]) |
| `Keeper` | `0x6B5815467da09DaA7DC83Db21c9239d98Bb487b5` |
| `VaultsRegistry` | `0x3a0008a588772446f6e656133C2D5029CC4FC20E` |

Check each on Etherscan before signing anything that touches it.

---

## 3. Infrastructure

| Item | Spec / version |
|---|---|
| Server | 1 dedicated server. The Operator Service "checks for at least 16 GB of RAM and 2 TB of free disk space at startup" [intro]; size for execution + consensus + validator client too: e.g. Hetzner AX102 (Ryzen 9 7950X3D, 128 GB DDR5 ECC, 2×1.92 TB NVMe) — specs from hetzner.com, 2026-10-08; price was not rendered on the page, quote at order time. |
| Execution client | one of Nethermind, Besu, Erigon, Geth, Reth — "must be fully synced and running" [nodes] |
| Consensus + validator client | one of Lighthouse, Nimbus, Prysm, Teku, Lodestar [nodes] |
| MEV-Boost | DAO-approved relays only (Smoothing Pool) [sp] |
| Operator Service | **v5.1.1** (latest stable release, GitHub 2026-09-28) |

Firewall: only the execution/consensus P2P ports open; RPC, Beacon API and the
operator metrics port (`9100`) bound to localhost or a VPN.

---

## 4. Create the vault on mainnet

Rehearse §4–§7 on **Hoodi** first: enable testnets in app Settings and pick
Hoodi [create]. Then repeat on mainnet.

### Path A — StakeWise app (recommended)

1. Connect the **deployer** hardware wallet to https://app.stakewise.io → **Operate** [create].
2. Vault type: **Regular Vault**; enable **Block list**; leave Private and ERC-20 Token **off**.
3. Vault capacity: **unlimited**. Vault fee: **99** (%).
4. Block rewards: **Smoothing Pool**.
5. Branding: name `Gatewayz`, short description, logo (editable later).
6. Review the summary — **fee shows 99%, Smoothing Pool, Block list on** — then **[SIGN]** the deploy transaction.
7. Copy the vault address from the app URL or **Details → Contract address** [create]. Record it as `<VAULT_ADDRESS>`.

### Path B — direct factory call (if the app cannot be used)

`createVault(bytes params, bool isOwnMevEscrow)` is payable and needs a
**security deposit of at least 1 gwei** (`_securityDeposit = 1e9`,
[core] `VaultEthStaking.sol:30,108`). `params` is
`abi.encode(EthVaultInitParams{capacity, feePercent, metadataIpfsHash})`;
capacity `type(uint256).max` = unlimited, `0` reverts ([core]
`VaultState.sol:336-339`); `isOwnMevEscrow = false` = Smoothing Pool.

Build the calldata offline (uses `eth_abi`, already a backend dependency):

```bash
uv run python - <<'EOF'
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector as sel
params = encode(["(uint256,uint16,string)"], [(2**256 - 1, 9900, "")])
data = sel("createVault(bytes,bool)") + encode(["bytes", "bool"], [params, False])
print("0x" + data.hex())
EOF
```

Then from the deployer wallet **[SIGN]** a transaction to
`BlocklistVaultFactory` with `value = 1000000000` wei and that `data`. The new
vault address is the `vault` field of the `VaultCreated(admin, vault,
ownMevEscrow, params)` event. Set metadata later with `setMetadata` (admin).

---

## 5. Hand off roles (deployer signs each, admin change last)

Do these **before** setting any Gatewayz env var, so the backend's first
reading already sees the final fee recipient.

1. **[SIGN]** `setFeeRecipient(<TREASURY_SAFE>)` — vault → Etherscan **Write as Proxy**, or app Settings.
2. **[SIGN]** Validators manager = `<OPERATOR_WALLET>`: app → **Operate** → vault → **Settings** → **Roles** → "Validators manager" → **Save** [vm].
3. **[SIGN]** `setBlocklistManager(<ADMIN_SAFE>)`.
4. **[SIGN]** `setAdmin(<ADMIN_SAFE>)` — **last**: after this the deployer can do nothing.
5. From the Admin Safe, confirm control with a harmless change (e.g. `setMetadata`) **[SIGN ×2-of-3]**.

---

## 6. Validators and the Operator Service

All commands are from [svc], [keys], [vm], [start] (Operator Service v5.1.1).

1. Install the binary:
   ```bash
   curl -sSfL https://raw.githubusercontent.com/stakewise/v3-operator/master/scripts/install.sh | sh -s
   ```
   or Docker: `docker pull europe-west4-docker.pkg.dev/stakewiselabs/public/v3-operator:v5.1.1`.
2. Initialise and create validator keys (enter `<VAULT_ADDRESS>`, number of keys, and the mnemonic when prompted):
   ```bash
   ./operator init
   ./operator create-keys
   ```
   Keystores land in `~/.stakewise/<VAULT_ADDRESS>/keystores`. Write the mnemonic down offline; protect `password.txt` the same way [keys].
3. Operator wallet (the validators manager from §5.2): `./operator create-wallet` (vault address + mnemonic), or set `WALLET_PRIVATE_KEY`. Fund it with ~0.1 ETH [vm].
4. Import the keystores into the validator client (its own import guide) and set **`suggested-fee-recipient` = `0x48319f97E5Da1233c21c48b80097c0FB7a20Ff86`** (Smoothing Pool). "Setting the wrong address forfeits your Vault's locked share of the pool" [sp].
5. Start the service with harvesting and metrics on:
   ```bash
   ./operator start --vault=<VAULT_ADDRESS> \
     --consensus-endpoints=<BEACON_URL> --execution-endpoints=<EL_URL> \
     --harvest-vault --enable-metrics
   ```
   `--harvest-vault` "calls `updateState` every 12 hours" [start]. Fees are only minted on harvest, so without it revenue appears only when users interact.
6. **Seed stake.** A validator only enters the activation queue once its effective
   balance is **≥ 32 ETH** (`MIN_ACTIVATION_BALANCE`, [spec]
   `is_eligible_for_activation_queue`). The service registers once the vault holds
   `--min-deposit-amount-gwei` (default 10 ETH, minimum 1 ETH [start]). Until a
   validator is **active**, the vault earns nothing and our ETH revenue is zero.
   Decide with finance whether the company seeds 32 ETH; like any staker, a company
   deposit keeps 1% of its rewards and the other 99% is minted to the Treasury Safe. Never link the seeding wallet to a Gatewayz account.
7. Monitoring: scrape `http://127.0.0.1:9100/metrics` (prefix `sw_operator_`, 30 s interval) [mon]. Alert on: service not ready, wallet balance < 0.05 ETH, exception count rising, unused validator keys = 0.

Never run the same validator keys in two validator clients at once (slashing).

---

## 7. Verify on-chain

On Etherscan → `<VAULT_ADDRESS>` → **Read as Proxy**:

| Call | Expected |
|---|---|
| `feePercent()` | `9900` |
| `feeRecipient()` | `<TREASURY_SAFE>` |
| `admin()` | `<ADMIN_SAFE>` |
| `validatorsManager()` | `<OPERATOR_WALLET>` |
| `blocklistManager()` | `<ADMIN_SAFE>` |
| `capacity()` | `115792089237316195423570985008687907853269984665640564039457584007913129639935` (unlimited) |
| `mevEscrow()` | `0x48319f97E5Da1233c21c48b80097c0FB7a20Ff86` |
| `getShares(<TREASURY_SAFE>)` | `0` before the first harvest with active validators, then growing |

And on beaconcha.in: at least one validator whose withdrawal credentials end in
`<VAULT_ADDRESS>` (the vault registers validators with
`withdrawalCredsPrefix ‖ 0x00…00 ‖ address(this)`, [core]
`ValidatorUtils.sol:89`), status **active**.

---

## 8. Configure Gatewayz and confirm

Railway → service **`api`** → Variables (see LAUNCH_CHECKLIST.md for the full set):

| Var | Value |
|---|---|
| `STAKEWISE_VAULT_ADDRESS` | `<VAULT_ADDRESS>` (checksummed or lower-case; an invalid value reads as unset) |
| `STAKEWISE_VAULT_CHAIN_ID` | `1` (the frontend only renders the ETH tab for chain 1) |
| `ALCHEMY_API_KEY` or `ETHEREUM_RPC_URL` | recommended: the vault reader otherwise uses public RPCs (`src/services/holdings/chains.py`) |

Confirm after the redeploy:

```bash
curl -s https://api.gatewayz.ai/delegation/status | jq '.data.eth'
# master flag still off:  {"vault_address":"<VAULT_ADDRESS>","chain_id":1,"fee_percent":null}
# after DELEGATED_STAKING_ENABLED=true:  "fee_percent":"99.00"   (cached 10 min)
curl -s -H "Authorization: Bearer <SUPERADMIN_API_KEY>" \
  https://api.gatewayz.ai/admin/delegation/rates | jq '.data.rates[] | select(.asset=="eth")'
# "configured": true
```

`fee_percent` is `null` while the master flag is off, or if the vault read fails
(check the RPC vars).

---

## 9. Never do

- **Never move the fee recipient's vault shares** (no `enterExitQueue`, no osETH
  minting against them, no transfer). Reconciliation computes ETH revenue as the
  day-over-day growth of `getShares(feeRecipient)` and floors a fall at zero
  (`src/services/delegation/reconciliation.py`), so moved shares read as **zero
  revenue** and can pause ETH accruals.
- Never call `setFeeRecipient` after launch without planning it: the backend
  restarts from a zero baseline for the new recipient (revenue before it is lost).
- Never create the vault with a low fee "to start" — see §1.1.
- Never link the treasury, admin, deployer, operator or seeding wallet to a Gatewayz account.
- Never set a validator client fee recipient other than the Smoothing Pool address.
- Never run the same validator keys on two machines.
- Never paste a private key, mnemonic or keystore password into a ticket, chat, agent session or the vault notes.
