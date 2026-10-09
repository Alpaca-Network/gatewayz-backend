# Counsel brief: Gatewayz delegated staking ("inference-as-yield")

> **Not legal advice.** Prepared by Gatewayz engineering on 2026-10-08 to scope
> questions for outside counsel. Nothing here is a legal conclusion. Sources were
> opened on 2026-10-08.

## 1. Product in one paragraph

Gatewayz sells AI inference (pay-per-use credits; 1 credit = $1 of usage). We
want users who hold ETH or ADA to **stake through Gatewayz-operated
infrastructure from their own wallets**. Gatewayz keeps ~99–100% of the staking
rewards that infrastructure earns, and separately grants the user **inference
credits** at a rate Gatewayz sets ("99% off inference up to $X/month"). The
software is built and switched off in production; nothing has launched.

## 2. How it works and who controls what

| | ETH | ADA |
|---|---|---|
| Where the user stakes | A StakeWise V3 vault (smart contract) on Ethereum that **Gatewayz deploys and administers**; the user deposits ETH and receives non-transferable vault shares | The user's wallet **delegates** to a stake pool **Gatewayz operates**; ADA never leaves the user's wallet |
| User keeps | Control of their vault shares; can exit through the vault's exit queue at any time (public vaults: 15-hour claim delay after processing) | Their ADA and keys; can redelegate or spend at any time |
| Gatewayz holds | Vault **admin** (multisig), **fee recipient** (treasury), validator signing keys | Pool cold/operational keys, reward account |
| Gatewayz can | Set the fee (rate-limited increases), block addresses from **depositing**, set roles, upgrade only to the next StakeWise-registered version of the same vault type (`VaultVersion._authorizeUpgrade`) | Set margin / fixed cost / pledge |
| Gatewayz cannot | Withdraw or move a user's vault shares or ETH | Move a user's ADA |
| How we earn | Vault fee **99%** (max allowed 100%) of rewards, minted to our treasury as vault shares | Pool fixed cost (170 ADA/epoch minimum) + margin **99%** of the rest |

In the vocabulary of the EBA/ESMA report below, the user keeps the **withdrawal**
keys and Gatewayz holds the **signing** keys.

**The credits.** Each day the backend measures each linked wallet's staked
amount in USD and grants `amount / 1000 × rate` credits, capped per account and
in total. The rate is set by a Gatewayz admin, can change or stop at any time,
is the same for everyone, and is **not** computed from the user's own protocol
rewards. At our planned settings, the credits' face value is about **1.25×**
the staking revenue they are funded from (they cost us 80% of face value).
Credits are spendable only on Gatewayz inference. Every public response carries:
"Rates are set by Gatewayz, can change at any time, and are not a guaranteed
return." A daily job pauses the program automatically if credit cost outruns
staking revenue.

## 3. Sources

**SEC/CFTC, Release Nos. 33-11412; 34-105020, "Application of the Federal
Securities Laws to Certain Types of Crypto Assets and Certain Transactions
Involving Crypto Assets"** — issued 2026-03-17, Federal Register 2026-03-23,
"Effective Date: March 23, 2026" (https://www.sec.gov/files/rules/interp/2026/33-11412.pdf).
Operative lines:

- "Protocol Staking Activities, in the manner and under the circumstances
  described in this release, do not involve the offer and sale of a security
  within the meaning of section 2(a)(1) of the Securities Act or section 3(a)(10)
  of the Exchange Act." (p. 47)
- Self-custodial staking with a third party: "When self-custodial staking
  directly with a third party, the Owner retains ownership and control of its
  digital commodities and its private keys." (p. 42) … "the Node Operator does not
  guarantee or otherwise set or fix the amount of the rewards owed to Owners,
  although the Node Operator may subtract from such amount its fees (whether
  fixed or a percentage of such amount)." (p. 49)
- Footnotes 124 and 126: if a Custodian / Liquid Staking Provider "does guarantee
  or otherwise set the amount of rewards owed to the Depositors, its activities
  are outside the scope of this release."
- Ancillary Services include "Alternate Rewards Payment Schedules and Amounts …
  provided the reward amounts are not fixed, guaranteed, or greater than those
  awarded by the PoS Network's software protocol." (p. 51) Footnote 127: services
  not discussed "are outside the scope of this release."

**EBA/ESMA, "Joint Report on recent developments in crypto-assets (Article 142
MiCAR)"**, 16 January 2025
(https://www.esma.europa.eu/sites/default/files/2025-01/ESMA75-453128700-1391_Joint_Report_on_recent_developments_in_crypto-assets__Art_142_MiCA_.pdf):

- "Lending, borrowing and staking are not explicitly captured under the
  definition of crypto-asset services set forth in MiCAR."
- "non-custodial staking services (such as pooled staking) involve a transfer of
  signing keys to a third-party (while retaining the withdrawal keys) and
  custodial staking services involve transferring both sets of keys to the
  provider."

## 4. Questions for counsel

1. **Fit with Release 33-11412.** Does ADA delegation fit "Self-Custodial Staking
   Directly with a Third Party", and the StakeWise vault fit "Liquid Staking"
   through a "protocol-based" provider — with Gatewayz as vault admin, fee
   recipient and node operator? Does administering the vault make us a
   "Liquid Staking Provider" or "Custodian"?
2. **The credits.** Is an admin-set, changeable credit grant per dollar staked —
   not derived from each user's protocol rewards — "set[ting] … the amount of
   the rewards" (fn. 124/126), or a separate commercial discount on our own
   service? Does the analysis change if we instead granted a share of *actual*
   revenue?
3. **"Not greater than" the protocol's rewards.** Credits' face value is ≈ 1.25×
   the underlying staking revenue. Is the Ancillary Services test measured at
   face value or at our cost? Must we cap face value at the user's own
   protocol-level reward?
4. **Fee of 99–100%.** Any issue with retaining almost all rewards, and what
   disclosures are required? Is 99% preferable to 100% (the user still receives a
   small protocol reward)?
5. **Credits as value.** Non-transferable, inference-only, no cash-out, can be
   changed or ended: any stored-value, money-transmission, or consumer-protection
   ("free", "99% off", rate-change notice) issues? Required terms of service?
6. **Sanctions/AML.** As vault admin with a blocklist and as pool operator, must we
   screen depositor/delegator addresses (OFAC) or block sanctioned addresses? Any
   KYC duty when credits attach to a user account?
7. **Jurisdictions.** Which users may we offer this to — US only, US + Canada (any
   Canadian securities or CSA staking position to check), EU (staking outside
   MiCAR services per EBA/ESMA, but national regimes)? Do we need geofencing?
8. **Slashing and operational loss.** Vault slashing reduces all depositors' ETH.
   What liability and disclosure follow? Should we offer slashing coverage
   (listed as an Ancillary Service)?
9. **Tax.** How do we book fee shares / pool fees, and do users have income from
   credits (1099 or similar reporting)? (May go to accountants.)
10. **Marketing.** Approve the user-facing copy and disclaimer; any words we must
    never use (we already ban APY/APR, "interest", and "return on your stake").
