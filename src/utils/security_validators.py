"""
Security validation utilities for URLs, webhooks, and sensitive data handling.

This module provides validation functions to prevent common security issues:
- SSRF (Server-Side Request Forgery) attacks
- Open redirect vulnerabilities
- Webhook authenticity verification
- Email address validation (RFC 5321/5322 compliance)
"""

import hashlib
import hmac
import logging
import re

logger = logging.getLogger(__name__)


# Comprehensive list of known temporary/disposable email domains
# These services provide throwaway email addresses commonly used for spam and abuse
TEMPORARY_EMAIL_DOMAINS = frozenset(
    {
        # Popular temporary email services
        "10minutemail.com",
        "10minutemail.net",
        "10minutemail.org",
        "10minemail.com",
        "20minutemail.com",
        "33mail.com",
        "tempmail.com",
        "temp-mail.org",
        "temp-mail.io",
        "tempmailo.com",
        "tempail.com",
        "tempr.email",
        "tempinbox.com",
        "guerrillamail.com",
        "guerrillamail.net",
        "guerrillamail.org",
        "guerrillamail.biz",
        "guerrillamail.de",
        "guerrillamail.info",
        "grr.la",
        "sharklasers.com",
        "mailinator.com",
        "mailinator.net",
        "mailinator.org",
        "mailinator2.com",
        "mailinater.com",
        "maildrop.cc",
        "mailnesia.com",
        "mailnull.com",
        "throwaway.email",
        "throwawaymail.com",
        "throam.com",
        "trashmail.com",
        "trashmail.net",
        "trashmail.org",
        "trashmail.me",
        "trashemail.de",
        "trashymail.com",
        "fakeinbox.com",
        "fakemailgenerator.com",
        "fakemail.net",
        "dispostable.com",
        "disposableemailaddresses.com",
        "disposable.com",
        "disposableinbox.com",
        "emailondeck.com",
        "getnada.com",
        "nada.email",
        "getairmail.com",
        "yopmail.com",
        "yopmail.fr",
        "yopmail.net",
        "cool.fr.nf",
        "jetable.fr.nf",
        "nospam.ze.tc",
        "nomail.xl.cx",
        "mega.zik.dj",
        "speed.1s.fr",
        "courriel.fr.nf",
        "moncourrier.fr.nf",
        "monemail.fr.nf",
        "monmail.fr.nf",
        "hide.biz.st",
        "mytrashmail.com",
        "mt2009.com",
        "trash2009.com",
        "1chuan.com",
        "1zhuan.com",
        "tempsky.com",
        "spamgourmet.com",
        "spamgourmet.net",
        "spamgourmet.org",
        "spambox.us",
        "spamfree24.org",
        "spamfree24.de",
        "spamfree24.eu",
        "spamfree24.info",
        "spamfree24.net",
        "spamherelots.com",
        "spamhereplease.com",
        "mailcatch.com",
        "mailexpire.com",
        "mailmoat.com",
        "mailscrap.com",
        "mailslite.com",
        "mailzilla.com",
        "mailzilla.org",
        "bugmenot.com",
        "bumpymail.com",
        "classicmail.co.za",
        "deadaddress.com",
        "devnullmail.com",
        "dodgeit.com",
        "dodgemail.de",
        "dodgit.com",
        "dontreg.com",
        "dontsendmespam.de",
        "dump-email.info",
        "dumpmail.de",
        "dumpyemail.com",
        "e4ward.com",
        "email60.com",
        "emailigo.de",
        "emailinfive.com",
        "emaillime.com",
        "emailmiser.com",
        "emailsensei.com",
        "emailtemporario.com.br",
        "emailwarden.com",
        "emailx.at.hm",
        "emailxfer.com",
        "emz.net",
        "enterto.com",
        "ephemail.net",
        "etranquil.com",
        "etranquil.net",
        "etranquil.org",
        "evopo.com",
        "explodemail.com",
        "express.net.ua",
        "eyepaste.com",
        "fastacura.com",
        "fastchevy.com",
        "fastchrysler.com",
        "fastkawasaki.com",
        "fastmazda.com",
        "fastmitsubishi.com",
        "fastnissan.com",
        "fastsubaru.com",
        "fastsuzuki.com",
        "fasttoyota.com",
        "fastyamaha.com",
        "filzmail.com",
        "fizmail.com",
        "flyspam.com",
        "footard.com",
        "frapmail.com",
        "friendlymail.co.uk",
        "front14.org",
        "fuckingduh.com",
        "fudgerub.com",
        "garliclife.com",
        "gehensiull.com",
        "ghosttexter.de",
        "gishpuppy.com",
        "gowikibooks.com",
        "gowikicampus.com",
        "gowikicars.com",
        "gowikifilms.com",
        "gowikigames.com",
        "gowikimusic.com",
        "gowikinetwork.com",
        "gowikitravel.com",
        "gowikitv.com",
        "great-host.in",
        "greensloth.com",
        "gsrv.co.uk",
        "haltospam.com",
        "hatespam.org",
        "hidemail.de",
        "hidzz.com",
        "hmamail.com",
        "hochsitze.com",
        "hopemail.biz",
        "hotpop.com",
        "hulapla.de",
        "ieatspam.eu",
        "ieatspam.info",
        "ihateyoualot.info",
        "imails.info",
        "imgof.com",
        "imgv.de",
        "imstations.com",
        "inbax.tk",
        "inbox.si",
        "inbox2.info",
        "incognitomail.com",
        "incognitomail.net",
        "incognitomail.org",
        "infocom.zp.ua",
        "inoutmail.de",
        "inoutmail.eu",
        "inoutmail.info",
        "inoutmail.net",
        "insorg-mail.info",
        "ipoo.org",
        "irish2me.com",
        "iwi.net",
        "jetable.com",
        "jetable.de",
        "jetable.fr",
        "jetable.net",
        "jetable.org",
        "jnxjn.com",
        "jourrapide.com",
        "jsrsolutions.com",
        "junk1.com",
        "kasmail.com",
        "kaspop.com",
        "keepmymail.com",
        "killmail.com",
        "killmail.net",
        "kimsdisk.com",
        "kingsq.ga",
        "kir.ch.tc",
        "klassmaster.com",
        "klassmaster.net",
        "klzlv.com",
        "kulturbetrieb.info",
        "kurzepost.de",
        "lackmail.net",
        "lags.us",
        "landmail.co",
        "lastmail.co",
        "lavabit.com",
        "letthemeatspam.com",
        "lhsdv.com",
        "lifebyfood.com",
        "link2mail.net",
        "litedrop.com",
        "loadby.us",
        "login-email.ml",
        "lol.ovpn.to",
        "lookugly.com",
        "lortemail.dk",
        "lovemeleaveme.com",
        "lr78.com",
        "lroid.com",
        "lukop.dk",
        "m4ilweb.info",
        "maboard.com",
        "mail-hierarchie.net",
        "mail-temporaire.fr",
        "mail.by",
        "mail.mezimages.net",
        "mail.zp.ua",
        "mail1a.de",
        "mail21.cc",
        "mail2rss.org",
        "mail333.com",
        "mail4trash.com",
        "mailbidon.com",
        "mailblocks.com",
        "mailde.de",
        "mailde.info",
        "maildx.com",
        "mailed.ro",
        "mailfa.tk",
        "mailforspam.com",
        "mailfree.ga",
        "mailfree.gq",
        "mailfree.ml",
        "mailfreeonline.com",
        "mailfs.com",
        "mailguard.me",
        "mailhazard.com",
        "mailhazard.us",
        "mailhz.me",
        "mailimate.com",
        "mailin8r.com",
        "mailincubator.com",
        "mailismagic.com",
        "mailjunk.cf",
        "mailjunk.ga",
        "mailjunk.gq",
        "mailjunk.ml",
        "mailjunk.tk",
        "mailmate.com",
        "mailme.gq",
        "mailme.ir",
        "mailme.lv",
        "mailme24.com",
        "mailmetrash.com",
        "mailna.biz",
        "mailna.co",
        "mailna.in",
        "mailna.me",
        "mailnator.com",
        "mailorg.org",
        "mailpick.biz",
        "mailrock.biz",
        "mailsac.com",
        "mailseal.de",
        "mailshell.com",
        "mailsiphon.com",
        "mailslapping.com",
        "mailsource.info",
        "mailtemp.info",
        "mailtothis.com",
        "makemetheking.com",
        "manifestgenerator.com",
        "manybrain.com",
        "mbx.cc",
        "meinspamschutz.de",
        "meltmail.com",
        "messagebeamer.de",
        "mezimages.net",
        "mierdamail.com",
        "migmail.pl",
        "migumail.com",
        "mintemail.com",
        "mjukgansen.nu",
        "moakt.com",
        "mobi.web.id",
        "mobileninja.co.uk",
        "moburl.com",
        "mohmal.com",
        "msa.minsmail.com",
        "mx0.wwwnew.eu",
        "myalias.pw",
        "mycleaninbox.net",
        "mypartyclip.de",
        "myphantomemail.com",
        "myspaceinc.com",
        "myspaceinc.net",
        "myspacepimpedup.com",
        "mytempemail.com",
        "mytempmail.com",
        "nabuma.com",
        "neomailbox.com",
        "nepwk.com",
        "nervmich.net",
        "nervtmansen.de",
        "netmails.com",
        "netmails.net",
        "netzidiot.de",
        "neverbox.com",
        "nice-4u.com",
        "nincsmail.com",
        "nmail.cf",
        "noclickemail.com",
        "nogmailspam.info",
        "nomail2me.com",
        "nomorespamemails.com",
        "nospam4.us",
        "nospamfor.us",
        "nospammail.net",
        "nospamthanks.info",
        "notmailinator.com",
        "notsharingmy.info",
        "nowhere.org",
        "nowmymail.com",
        "ntlhelp.net",
        "nurfuerspam.de",
        "nus.edu.sg",
        "nwldx.com",
        "objectmail.com",
        "obobbo.com",
        "odnorazovoe.ru",
        "ohaaa.de",
        "omail.pro",
        "oneoffemail.com",
        "onewaymail.com",
        "onlatedotcom.info",
        "online.ms",
        "oopi.org",
        "opayq.com",
        "ordinaryamerican.net",
        "otherinbox.com",
        "ourklips.com",
        "outlawspam.com",
        "ovpn.to",
        "owlpic.com",
        "pancakemail.com",
        "pjjkp.com",
        "plexolan.de",
        "poczta.onet.pl",
        "politikerclub.de",
        "poofy.org",
        "pookmail.com",
        "privacy.net",
        "privatdemail.net",
        "proxymail.eu",
        "prtnx.com",
        "punkass.com",
        "putthisinyourspamdatabase.com",
        "pwrby.com",
        "qisdo.com",
        "qisoa.com",
        "quickinbox.com",
        "quickmail.nl",
        "rainmail.biz",
        "rcpt.at",
        "reallymymail.com",
        "realtyalerts.ca",
        "recode.me",
        "recursor.net",
        "recyclemail.dk",
        "regbypass.com",
        "regbypass.comsafe-mail.net",
        "rejectmail.com",
        "remail.cf",
        "remail.ga",
        "rhyta.com",
        "rklips.com",
        "rmqkr.net",
        "royal.net",
        "rppkn.com",
        "rtrtr.com",
        "s0ny.net",
        "safe-mail.net",
        "safersignup.de",
        "safetymail.info",
        "safetypost.de",
        "sandelf.de",
        "saynotospams.com",
        "schafmail.de",
        "schrott-email.de",
        "secretemail.de",
        "selfdestructingmail.com",
        "sendspamhere.com",
        "senseless-entertainment.com",
        "sharedmailbox.org",
        "shieldemail.com",
        "shiftmail.com",
        "shitmail.me",
        "shitmail.org",
        "shitware.nl",
        "shortmail.net",
        "shut.name",
        "shut.ws",
        "sibmail.com",
        "sinnlos-mail.de",
        "siteposter.net",
        "skeefmail.com",
        "slaskpost.se",
        "slave-auctions.net",
        "slopsbox.com",
        "slowslow.de",
        "slushmail.com",
        "smashmail.de",
        "smellfear.com",
        "smellrear.com",
        "smoug.net",
        "snakemail.com",
        "sneakemail.com",
        "sneakmail.de",
        "snkmail.com",
        "sofimail.com",
        "sofort-mail.de",
        "sogetthis.com",
        "soisz.com",
        "solvemail.info",
        "soodonims.com",
        "spam.la",
        "spam.su",
        "spam4.me",
        "spamail.de",
        "spamarrest.com",
        "spamavert.com",
        "spambob.com",
        "spambob.net",
        "spambob.org",
        "spambog.com",
        "spambog.de",
        "spambog.net",
        "spambog.ru",
        "spambox.info",
        "spambox.irishspringrealty.com",
        "spamcannon.com",
        "spamcannon.net",
        "spamcero.com",
        "spamcon.org",
        "spamcorptastic.com",
        "spamcowboy.com",
        "spamcowboy.net",
        "spamcowboy.org",
        "spamday.com",
        "spamex.com",
        "spamfighter.cf",
        "spamfighter.ga",
        "spamfighter.gq",
        "spamfighter.ml",
        "spamfighter.tk",
        "spamfree.eu",
        "spamfree24.com",
        "spamgoes.in",
        "spamhole.com",
        "spamify.com",
        "spaminator.de",
        "spamkill.info",
        "spaml.com",
        "spaml.de",
        "spamlot.net",
        "spammotel.com",
        "spamobox.com",
        "spamoff.de",
        "spamsalad.in",
        "spamslicer.com",
        "spamspot.com",
        "spamstack.net",
        "spamthis.co.uk",
        "spamthisplease.com",
        "spamtrail.com",
        "spamtroll.net",
        "spoofmail.de",
        "squizzy.de",
        "ssoia.com",
        "startkeys.com",
        "stinkefinger.net",
        "stop-my-spam.cf",
        "stop-my-spam.com",
        "stop-my-spam.ga",
        "stop-my-spam.ml",
        "stop-my-spam.tk",
        "streetwisemail.com",
        "stuffmail.de",
        "supergreatmail.com",
        "supermailer.jp",
        "superrito.com",
        "superstachel.de",
        "suremail.info",
        "svk.jp",
        "sweetxxx.de",
        "tafmail.com",
        "tagyourself.com",
        "talkinator.com",
        "tapchicuoihoi.com",
        "teewars.org",
        "teleosaurs.xyz",
        "tellos.xyz",
        "temp.emeraldwebmail.com",
        "temp.headstrong.de",
        "tempalias.com",
        "tempemail.biz",
        "tempemail.co.za",
        "tempemail.com",
        "tempemail.net",
        "tempinbox.co.uk",
        "tempmail.co",
        "tempmail.de",
        "tempmail.eu",
        "tempmail.it",
        "tempmail.net",
        "tempmail.us",
        "tempmail2.com",
        "tempmaildemo.com",
        "tempmailer.com",
        "tempmailer.de",
        "tempomail.fr",
        "temporarioemail.com.br",
        "temporaryemail.net",
        "temporaryemail.us",
        "temporaryforwarding.com",
        "temporaryinbox.com",
        "temporarymailaddress.com",
        "tempthe.net",
        "thanksnospam.info",
        "thankyou2010.com",
        "thecloudindex.com",
        "thelimestones.com",
        "thisisnotmyrealemail.com",
        "thismail.net",
        "thismail.ru",
        "throwawayemailaddress.com",
        "tilien.com",
        "tittbit.in",
        "tmailinator.com",
        "toiea.com",
        "toomail.biz",
        "topranklist.de",
        "tradermail.info",
        "trash-amil.com",
        "trash-mail.at",
        "trash-mail.com",
        "trash-mail.de",
        "trash-mail.ga",
        "trash-mail.gq",
        "trash-mail.ml",
        "trash-mail.tk",
        "trash2010.com",
        "trash2011.com",
        "trashbox.eu",
        "trashdevil.com",
        "trashdevil.de",
        "trashmail.at",
        "trashmail.de",
        "trashmail.io",
        "trashmail.ws",
        "trashmailer.com",
        "trashymail.net",
        "trbvm.com",
        "trickmail.net",
        "trillianpro.com",
        "tryalert.com",
        "turual.com",
        "twinmail.de",
        "tyldd.com",
        "uggsrock.com",
        "umail.net",
        "upliftnow.com",
        "uplipht.com",
        "uroid.com",
        "us.af",
        "valemail.net",
        "venompen.com",
        "veryrealemail.com",
        "viditag.com",
        "viewcastmedia.com",
        "viewcastmedia.net",
        "viewcastmedia.org",
        "viralplays.com",
        "vkcode.ru",
        "vpn.st",
        "vsimcard.com",
        "vubby.com",
        "wasteland.rfc822.org",
        "webemail.me",
        "webm4il.info",
        "webuser.in",
        "wee.my",
        "weg-werf-email.de",
        "wegwerf-email-addressen.de",
        "wegwerf-emails.de",
        "wegwerfadresse.de",
        "wegwerfemail.com",
        "wegwerfemail.de",
        "wegwerfmail.de",
        "wegwerfmail.info",
        "wegwerfmail.net",
        "wegwerfmail.org",
        "wetrainbayarea.com",
        "wetrainbayarea.org",
        "wh4f.org",
        "whatiaas.com",
        "whatpaas.com",
        "whopy.com",
        "whtjddn.33mail.com",
        "whyspam.me",
        "wilemail.com",
        "willhackforfood.biz",
        "willselfdestruct.com",
        "winemaven.info",
        "wolfsmail.tk",
        "wollan.info",
        "worldspace.link",
        "wronghead.com",
        "wuzup.net",
        "wuzupmail.net",
        "wwwnew.eu",
        "xagloo.com",
        "xemaps.com",
        "xents.com",
        "xmaily.com",
        "xoxy.net",
        "yapped.net",
        "yep.it",
        "yogamaven.com",
        "yomail.info",
        "yopmail.gq",
        "you-spam.com",
        "yourdomain.com",
        "ypmail.webarnak.fr.eu.org",
        "yuurok.com",
        "za.com",
        "zehnminuten.de",
        "zehnminutenmail.de",
        "zetmail.com",
        "zippymail.info",
        "zoaxe.com",
        "zoemail.com",
        "zoemail.net",
        "zoemail.org",
        "zomg.info",
        "zxcv.com",
        "zxcvbnm.com",
        "zzz.com",
    }
)

# Domains blocked due to abuse, spam, or suspicious activity
# These are legitimate email domains that have been identified as sources of abuse
BLOCKED_EMAIL_DOMAINS = frozenset(
    {
        # Added 2026-01-05: Bulk automated account creation abuse
        "rccg-clf.org",
    }
)


def is_blocked_email_domain(email: str) -> bool:
    """Check if email uses a domain that has been blocked due to abuse.

    These are domains that have been explicitly blocked due to:
    - Bulk automated account creation
    - Credit abuse patterns
    - Spam or malicious activity

    Unlike temporary email domains, these may be legitimate email providers
    that have been blocked due to specific abuse incidents.

    Args:
        email: Email address string to check

    Returns:
        True if email domain is blocked, False otherwise
    """
    if not email or "@" not in email:
        return False

    try:
        domain = email.split("@")[-1].lower().strip()
        return domain in BLOCKED_EMAIL_DOMAINS
    except Exception:
        return False


def is_temporary_email_domain(email: str) -> bool:
    """Check if email uses a known temporary/disposable email domain.

    These domains provide throwaway email addresses commonly used for:
    - Spam and bot registrations
    - Bypassing trial restrictions
    - Creating fake accounts

    Args:
        email: Email address string to check

    Returns:
        True if email domain is a known temporary email service, False otherwise

    Examples:
        >>> is_temporary_email_domain("user@tempmail.com")
        True
        >>> is_temporary_email_domain("user@gmail.com")
        False
        >>> is_temporary_email_domain("user@10minutemail.com")
        True
    """
    if not email or "@" not in email:
        return False

    try:
        domain = email.split("@")[-1].lower().strip()
        return domain in TEMPORARY_EMAIL_DOMAINS
    except Exception:
        return False


def is_valid_email(email: str) -> bool:
    """Validate email address according to RFC 5321/5322 standards.

    This function checks if an email address is valid and follows the standard email format.
    It uses a regex pattern that covers most common valid email addresses while rejecting
    clearly invalid ones (like those with colons in the local part).

    Args:
        email: Email address string to validate

    Returns:
        True if email is valid, False otherwise

    Examples:
        >>> is_valid_email("user@example.com")
        True
        >>> is_valid_email("did:privy:cmjk48www00vdl50dxh4s2pzd@privy.user")
        False
        >>> is_valid_email("test+tag@example.com")
        True
        >>> is_valid_email("invalid@")
        False
    """
    if not email or not isinstance(email, str):
        return False

    # RFC 5322 compliant email regex pattern
    # This pattern matches most valid email addresses while being strict enough
    # to reject invalid formats like those with colons or other special chars in local part
    email_pattern = re.compile(
        r"^[a-zA-Z0-9.!#$%&\'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
        r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
    )

    # Basic checks
    if "@" not in email:
        return False

    # Split into local and domain parts
    parts = email.rsplit("@", 1)
    if len(parts) != 2:
        return False

    local_part, domain_part = parts

    # Check length constraints (RFC 5321)
    if len(local_part) > 64 or len(domain_part) > 255:
        return False

    # Check for colons in local part (not allowed in standard email addresses)
    # This specifically catches Privy IDs like "did:privy:xxx@privy.user"
    if ":" in local_part:
        return False

    # Use regex to validate overall format
    return bool(email_pattern.match(email))


def generate_webhook_signature(payload: str, secret: str) -> str:
    """Generate HMAC-SHA256 signature for webhook payload.

    Args:
        payload: Webhook payload (JSON string)
        secret: Shared secret key

    Returns:
        Hex-encoded HMAC-SHA256 signature
    """
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_webhook_signature(
    payload: str, signature: str, secret: str, header_name: str = "X-Webhook-Signature"
) -> bool:
    """Verify webhook signature using constant-time comparison.

    Args:
        payload: Webhook payload (JSON string)
        signature: Signature from webhook header
        secret: Shared secret key
        header_name: Name of the signature header (for logging)

    Returns:
        True if signature is valid, False otherwise
    """
    try:
        expected = generate_webhook_signature(payload, secret)

        # Use constant-time comparison to prevent timing attacks
        import secrets as secrets_module

        if secrets_module.compare_digest(signature, expected):
            return True

        logger.warning(f"Invalid webhook signature in {header_name}")
        return False

    except Exception as e:
        logger.error(f"Error verifying webhook signature: {e}")
        return False


def sanitize_for_logging(value: str) -> str:
    """Sanitize user-controlled strings for safe logging.

    Prevents log injection attacks by removing newlines and other control characters
    that could be used to forge log entries.

    Args:
        value: String value to sanitize (can be None)

    Returns:
        Sanitized string with newlines replaced by spaces
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    # Replace newlines and carriage returns with spaces to prevent log injection
    return value.replace("\n", " ").replace("\r", " ").replace("\x00", "")

