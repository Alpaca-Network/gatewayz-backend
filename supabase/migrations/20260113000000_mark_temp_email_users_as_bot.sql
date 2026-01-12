-- Migration: Mark existing temporary email accounts as bot
-- This migration:
-- 1. Updates existing users with temporary email domains to have subscription_status='bot'
-- 2. Creates a scheduled job to mark expired trials as 'expired'
-- 3. Adds a function to check if an email is from a temporary domain

-- Create a table to store temporary email domains for efficient lookups
CREATE TABLE IF NOT EXISTS temporary_email_domains (
    domain TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create index for fast domain lookups
CREATE INDEX IF NOT EXISTS idx_temp_email_domains_domain ON temporary_email_domains(domain);

-- Insert all known temporary email domains
INSERT INTO temporary_email_domains (domain) VALUES
    '10minemail.com',
    '10minutemail.com',
    '10minutemail.net',
    '10minutemail.org',
    '1chuan.com',
    '1zhuan.com',
    '20minutemail.com',
    '33mail.com',
    'bugmenot.com',
    'bumpymail.com',
    'classicmail.co.za',
    'cool.fr.nf',
    'courriel.fr.nf',
    'deadaddress.com',
    'devnullmail.com',
    'disposable.com',
    'disposableemailaddresses.com',
    'disposableinbox.com',
    'dispostable.com',
    'dodgeit.com',
    'dodgemail.de',
    'dodgit.com',
    'dontreg.com',
    'dontsendmespam.de',
    'dump-email.info',
    'dumpmail.de',
    'dumpyemail.com',
    'e4ward.com',
    'email60.com',
    'emailigo.de',
    'emailinfive.com',
    'emaillime.com',
    'emailmiser.com',
    'emailondeck.com',
    'emailsensei.com',
    'emailtemporario.com.br',
    'emailwarden.com',
    'emailx.at.hm',
    'emailxfer.com',
    'emz.net',
    'enterto.com',
    'ephemail.net',
    'etranquil.com',
    'etranquil.net',
    'etranquil.org',
    'evopo.com',
    'explodemail.com',
    'express.net.ua',
    'eyepaste.com',
    'fakeinbox.com',
    'fakemail.net',
    'fakemailgenerator.com',
    'fastacura.com',
    'fastchevy.com',
    'fastchrysler.com',
    'fastkawasaki.com',
    'fastmazda.com',
    'fastmitsubishi.com',
    'fastnissan.com',
    'fastsubaru.com',
    'fastsuzuki.com',
    'fasttoyota.com',
    'fastyamaha.com',
    'filzmail.com',
    'fizmail.com',
    'flyspam.com',
    'footard.com',
    'frapmail.com',
    'friendlymail.co.uk',
    'front14.org',
    'fuckingduh.com',
    'fudgerub.com',
    'garliclife.com',
    'gehensiull.com',
    'getairmail.com',
    'getnada.com',
    'ghosttexter.de',
    'gishpuppy.com',
    'gowikibooks.com',
    'gowikicampus.com',
    'gowikicars.com',
    'gowikifilms.com',
    'gowikigames.com',
    'gowikimusic.com',
    'gowikinetwork.com',
    'gowikitravel.com',
    'gowikitv.com',
    'great-host.in',
    'greensloth.com',
    'grr.la',
    'gsrv.co.uk',
    'guerrillamail.biz',
    'guerrillamail.com',
    'guerrillamail.de',
    'guerrillamail.info',
    'guerrillamail.net',
    'guerrillamail.org',
    'haltospam.com',
    'hatespam.org',
    'hide.biz.st',
    'hidemail.de',
    'hidzz.com',
    'hmamail.com',
    'hochsitze.com',
    'hopemail.biz',
    'hotpop.com',
    'hulapla.de',
    'ieatspam.eu',
    'ieatspam.info',
    'ihateyoualot.info',
    'imails.info',
    'imgof.com',
    'imgv.de',
    'imstations.com',
    'inbax.tk',
    'inbox.si',
    'inbox2.info',
    'incognitomail.com',
    'incognitomail.net',
    'incognitomail.org',
    'infocom.zp.ua',
    'inoutmail.de',
    'inoutmail.eu',
    'inoutmail.info',
    'inoutmail.net',
    'insorg-mail.info',
    'ipoo.org',
    'irish2me.com',
    'iwi.net',
    'jetable.com',
    'jetable.de',
    'jetable.fr',
    'jetable.fr.nf',
    'jetable.net',
    'jetable.org',
    'jnxjn.com',
    'jourrapide.com',
    'jsrsolutions.com',
    'junk1.com',
    'kasmail.com',
    'kaspop.com',
    'keepmymail.com',
    'killmail.com',
    'killmail.net',
    'kimsdisk.com',
    'kingsq.ga',
    'kir.ch.tc',
    'klassmaster.com',
    'klassmaster.net',
    'klzlv.com',
    'kulturbetrieb.info',
    'kurzepost.de',
    'lackmail.net',
    'lags.us',
    'landmail.co',
    'lastmail.co',
    'lavabit.com',
    'letthemeatspam.com',
    'lhsdv.com',
    'lifebyfood.com',
    'link2mail.net',
    'litedrop.com',
    'loadby.us',
    'login-email.ml',
    'lol.ovpn.to',
    'lookugly.com',
    'lortemail.dk',
    'lovemeleaveme.com',
    'lr78.com',
    'lroid.com',
    'lukop.dk',
    'm4ilweb.info',
    'maboard.com',
    'mail-hierarchie.net',
    'mail-temporaire.fr',
    'mail.by',
    'mail.mezimages.net',
    'mail.zp.ua',
    'mail1a.de',
    'mail21.cc',
    'mail2rss.org',
    'mail333.com',
    'mail4trash.com',
    'mailbidon.com',
    'mailblocks.com',
    'mailcatch.com',
    'mailcatch.com',
    'mailde.de',
    'mailde.info',
    'maildrop.cc',
    'maildx.com',
    'mailed.ro',
    'mailexpire.com',
    'mailfa.tk',
    'mailforspam.com',
    'mailfree.ga',
    'mailfree.gq',
    'mailfree.ml',
    'mailfreeonline.com',
    'mailfs.com',
    'mailguard.me',
    'mailhazard.com',
    'mailhazard.us',
    'mailhz.me',
    'mailimate.com',
    'mailin8r.com',
    'mailinater.com',
    'mailinater.com',
    'mailinator.com',
    'mailinator.net',
    'mailinator.org',
    'mailinator2.com',
    'mailincubator.com',
    'mailismagic.com',
    'mailjunk.cf',
    'mailjunk.ga',
    'mailjunk.gq',
    'mailjunk.ml',
    'mailjunk.tk',
    'mailmate.com',
    'mailme.gq',
    'mailme.ir',
    'mailme.lv',
    'mailme24.com',
    'mailmetrash.com',
    'mailmoat.com',
    'mailna.biz',
    'mailna.co',
    'mailna.in',
    'mailna.me',
    'mailnator.com',
    'mailnesia.com',
    'mailnull.com',
    'mailnull.com',
    'mailorg.org',
    'mailpick.biz',
    'mailrock.biz',
    'mailsac.com',
    'mailscrap.com',
    'mailseal.de',
    'mailshell.com',
    'mailsiphon.com',
    'mailslapping.com',
    'mailslite.com',
    'mailsource.info',
    'mailtemp.info',
    'mailtothis.com',
    'mailzilla.com',
    'mailzilla.com',
    'mailzilla.org',
    'makemetheking.com',
    'manifestgenerator.com',
    'manybrain.com',
    'mbx.cc',
    'mega.zik.dj',
    'mega.zik.dj',
    'meinspamschutz.de',
    'meltmail.com',
    'messagebeamer.de',
    'mezimages.net',
    'mierdamail.com',
    'migmail.pl',
    'migumail.com',
    'mintemail.com',
    'mjukgansen.nu',
    'moakt.com',
    'mobi.web.id',
    'mobileninja.co.uk',
    'moburl.com',
    'mohmal.com',
    'moncourrier.fr.nf',
    'moncourrier.fr.nf',
    'monemail.fr.nf',
    'monemail.fr.nf',
    'monmail.fr.nf',
    'monmail.fr.nf',
    'msa.minsmail.com',
    'mt2009.com',
    'mt2009.com',
    'mx0.wwwnew.eu',
    'myalias.pw',
    'mycleaninbox.net',
    'mypartyclip.de',
    'myphantomemail.com',
    'myspaceinc.com',
    'myspaceinc.net',
    'myspacepimpedup.com',
    'mytempemail.com',
    'mytempmail.com',
    'mytrashmail.com',
    'mytrashmail.com',
    'nabuma.com',
    'nada.email',
    'neomailbox.com',
    'nepwk.com',
    'nervmich.net',
    'nervtmansen.de',
    'netmails.com',
    'netmails.net',
    'netzidiot.de',
    'neverbox.com',
    'nice-4u.com',
    'nincsmail.com',
    'nmail.cf',
    'noclickemail.com',
    'nogmailspam.info',
    'nomail.xl.cx',
    'nomail.xl.cx',
    'nomail2me.com',
    'nomorespamemails.com',
    'nospam.ze.tc',
    'nospam.ze.tc',
    'nospam4.us',
    'nospamfor.us',
    'nospammail.net',
    'nospamthanks.info',
    'notmailinator.com',
    'notsharingmy.info',
    'nowhere.org',
    'nowmymail.com',
    'ntlhelp.net',
    'nurfuerspam.de',
    'nus.edu.sg',
    'nwldx.com',
    'objectmail.com',
    'obobbo.com',
    'odnorazovoe.ru',
    'ohaaa.de',
    'omail.pro',
    'oneoffemail.com',
    'onewaymail.com',
    'onlatedotcom.info',
    'online.ms',
    'oopi.org',
    'opayq.com',
    'ordinaryamerican.net',
    'otherinbox.com',
    'ourklips.com',
    'outlawspam.com',
    'ovpn.to',
    'owlpic.com',
    'pancakemail.com',
    'pjjkp.com',
    'plexolan.de',
    'poczta.onet.pl',
    'politikerclub.de',
    'poofy.org',
    'pookmail.com',
    'privacy.net',
    'privatdemail.net',
    'proxymail.eu',
    'prtnx.com',
    'punkass.com',
    'putthisinyourspamdatabase.com',
    'pwrby.com',
    'qisdo.com',
    'qisoa.com',
    'quickinbox.com',
    'quickmail.nl',
    'rainmail.biz',
    'rccg-clf.org',
    'rcpt.at',
    'reallymymail.com',
    'realtyalerts.ca',
    'recode.me',
    'recursor.net',
    'recyclemail.dk',
    'regbypass.com',
    'regbypass.comsafe-mail.net',
    'rejectmail.com',
    'remail.cf',
    'remail.ga',
    'rhyta.com',
    'rklips.com',
    'rmqkr.net',
    'royal.net',
    'rppkn.com',
    'rtrtr.com',
    's0ny.net',
    'safe-mail.net',
    'safersignup.de',
    'safetymail.info',
    'safetypost.de',
    'sandelf.de',
    'saynotospams.com',
    'schafmail.de',
    'schrott-email.de',
    'secretemail.de',
    'selfdestructingmail.com',
    'sendspamhere.com',
    'senseless-entertainment.com',
    'sharedmailbox.org',
    'sharklasers.com',
    'sharklasers.com',
    'shieldemail.com',
    'shiftmail.com',
    'shitmail.me',
    'shitmail.org',
    'shitware.nl',
    'shortmail.net',
    'shut.name',
    'shut.ws',
    'sibmail.com',
    'sinnlos-mail.de',
    'siteposter.net',
    'skeefmail.com',
    'slaskpost.se',
    'slave-auctions.net',
    'slopsbox.com',
    'slowslow.de',
    'slushmail.com',
    'smashmail.de',
    'smellfear.com',
    'smellrear.com',
    'smoug.net',
    'snakemail.com',
    'sneakemail.com',
    'sneakmail.de',
    'snkmail.com',
    'sofimail.com',
    'sofort-mail.de',
    'sogetthis.com',
    'soisz.com',
    'solvemail.info',
    'soodonims.com',
    'spam.la',
    'spam.su',
    'spam4.me',
    'spamail.de',
    'spamarrest.com',
    'spamavert.com',
    'spambob.com',
    'spambob.net',
    'spambob.org',
    'spambog.com',
    'spambog.de',
    'spambog.net',
    'spambog.ru',
    'spambox.info',
    'spambox.irishspringrealty.com',
    'spambox.us',
    'spambox.us',
    'spamcannon.com',
    'spamcannon.net',
    'spamcero.com',
    'spamcon.org',
    'spamcorptastic.com',
    'spamcowboy.com',
    'spamcowboy.net',
    'spamcowboy.org',
    'spamday.com',
    'spamex.com',
    'spamfighter.cf',
    'spamfighter.ga',
    'spamfighter.gq',
    'spamfighter.ml',
    'spamfighter.tk',
    'spamfree.eu',
    'spamfree24.com',
    'spamfree24.de',
    'spamfree24.de',
    'spamfree24.eu',
    'spamfree24.eu',
    'spamfree24.info',
    'spamfree24.info',
    'spamfree24.net',
    'spamfree24.net',
    'spamfree24.org',
    'spamfree24.org',
    'spamgoes.in',
    'spamgourmet.com',
    'spamgourmet.net',
    'spamgourmet.org',
    'spamherelots.com',
    'spamherelots.com',
    'spamhereplease.com',
    'spamhereplease.com',
    'spamhole.com',
    'spamify.com',
    'spaminator.de',
    'spamkill.info',
    'spaml.com',
    'spaml.de',
    'spamlot.net',
    'spammotel.com',
    'spamobox.com',
    'spamoff.de',
    'spamsalad.in',
    'spamslicer.com',
    'spamspot.com',
    'spamstack.net',
    'spamthis.co.uk',
    'spamthisplease.com',
    'spamtrail.com',
    'spamtroll.net',
    'speed.1s.fr',
    'speed.1s.fr',
    'spoofmail.de',
    'squizzy.de',
    'ssoia.com',
    'startkeys.com',
    'stinkefinger.net',
    'stop-my-spam.cf',
    'stop-my-spam.com',
    'stop-my-spam.ga',
    'stop-my-spam.ml',
    'stop-my-spam.tk',
    'streetwisemail.com',
    'stuffmail.de',
    'supergreatmail.com',
    'supermailer.jp',
    'superrito.com',
    'superstachel.de',
    'suremail.info',
    'svk.jp',
    'sweetxxx.de',
    'tafmail.com',
    'tagyourself.com',
    'talkinator.com',
    'tapchicuoihoi.com',
    'teewars.org',
    'teleosaurs.xyz',
    'tellos.xyz',
    'temp-mail.io',
    'temp-mail.org',
    'temp.emeraldwebmail.com',
    'temp.headstrong.de',
    'tempail.com',
    'tempalias.com',
    'tempemail.biz',
    'tempemail.co.za',
    'tempemail.com',
    'tempemail.net',
    'tempinbox.co.uk',
    'tempinbox.com',
    'tempinbox.com',
    'tempmail.co',
    'tempmail.com',
    'tempmail.de',
    'tempmail.eu',
    'tempmail.it',
    'tempmail.net',
    'tempmail.us',
    'tempmail2.com',
    'tempmaildemo.com',
    'tempmailer.com',
    'tempmailer.de',
    'tempmailo.com',
    'tempomail.fr',
    'temporarioemail.com.br',
    'temporaryemail.net',
    'temporaryemail.us',
    'temporaryforwarding.com',
    'temporaryinbox.com',
    'temporarymailaddress.com',
    'tempr.email',
    'tempsky.com',
    'tempthe.net',
    'thanksnospam.info',
    'thankyou2010.com',
    'thecloudindex.com',
    'thelimestones.com',
    'thisisnotmyrealemail.com',
    'thismail.net',
    'thismail.ru',
    'throam.com',
    'throam.com',
    'throwaway.email',
    'throwawayemailaddress.com',
    'throwawaymail.com',
    'throwawaymail.com',
    'tilien.com',
    'tittbit.in',
    'tmailinator.com',
    'toiea.com',
    'toomail.biz',
    'topranklist.de',
    'tradermail.info',
    'trash-amil.com',
    'trash-mail.at',
    'trash-mail.com',
    'trash-mail.de',
    'trash-mail.ga',
    'trash-mail.gq',
    'trash-mail.ml',
    'trash-mail.tk',
    'trash2009.com',
    'trash2009.com',
    'trash2010.com',
    'trash2011.com',
    'trashbox.eu',
    'trashdevil.com',
    'trashdevil.de',
    'trashemail.de',
    'trashemail.de',
    'trashmail.at',
    'trashmail.com',
    'trashmail.com',
    'trashmail.de',
    'trashmail.io',
    'trashmail.me',
    'trashmail.me',
    'trashmail.net',
    'trashmail.net',
    'trashmail.org',
    'trashmail.org',
    'trashmail.ws',
    'trashmailer.com',
    'trashymail.com',
    'trashymail.com',
    'trashymail.net',
    'trbvm.com',
    'trickmail.net',
    'trillianpro.com',
    'tryalert.com',
    'turual.com',
    'twinmail.de',
    'tyldd.com',
    'uggsrock.com',
    'umail.net',
    'upliftnow.com',
    'uplipht.com',
    'uroid.com',
    'us.af',
    'valemail.net',
    'venompen.com',
    'veryrealemail.com',
    'viditag.com',
    'viewcastmedia.com',
    'viewcastmedia.net',
    'viewcastmedia.org',
    'viralplays.com',
    'vkcode.ru',
    'vpn.st',
    'vsimcard.com',
    'vubby.com',
    'wasteland.rfc822.org',
    'webemail.me',
    'webm4il.info',
    'webuser.in',
    'wee.my',
    'weg-werf-email.de',
    'wegwerf-email-addressen.de',
    'wegwerf-emails.de',
    'wegwerfadresse.de',
    'wegwerfemail.com',
    'wegwerfemail.de',
    'wegwerfmail.de',
    'wegwerfmail.info',
    'wegwerfmail.net',
    'wegwerfmail.org',
    'wetrainbayarea.com',
    'wetrainbayarea.org',
    'wh4f.org',
    'whatiaas.com',
    'whatpaas.com',
    'whopy.com',
    'whtjddn.33mail.com',
    'whyspam.me',
    'wilemail.com',
    'willhackforfood.biz',
    'willselfdestruct.com',
    'winemaven.info',
    'wolfsmail.tk',
    'wollan.info',
    'worldspace.link',
    'wronghead.com',
    'wuzup.net',
    'wuzupmail.net',
    'wwwnew.eu',
    'xagloo.com',
    'xemaps.com',
    'xents.com',
    'xmaily.com',
    'xoxy.net',
    'yapped.net',
    'yep.it',
    'yogamaven.com',
    'yomail.info',
    'yopmail.com',
    'yopmail.com',
    'yopmail.fr',
    'yopmail.fr',
    'yopmail.gq',
    'yopmail.net',
    'yopmail.net',
    'you-spam.com',
    'yourdomain.com',
    'ypmail.webarnak.fr.eu.org',
    'yuurok.com',
    'za.com',
    'zehnminuten.de',
    'zehnminutenmail.de',
    'zetmail.com',
    'zippymail.info',
    'zoaxe.com',
    'zoemail.com',
    'zoemail.net',
    'zoemail.org',
    'zomg.info',
    'zxcv.com',
    'zxcvbnm.com',
    'zzz.com'
ON CONFLICT (domain) DO NOTHING;

-- Create function to check if email is from temporary domain
CREATE OR REPLACE FUNCTION is_temporary_email(email_address TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    email_domain TEXT;
BEGIN
    -- Extract domain from email
    email_domain := LOWER(SPLIT_PART(email_address, '@', 2));

    -- Check if domain exists in temporary_email_domains table
    RETURN EXISTS (
        SELECT 1 FROM temporary_email_domains WHERE domain = email_domain
    );
END;
$$;

-- Grant execute permission
GRANT EXECUTE ON FUNCTION is_temporary_email(TEXT) TO service_role;
GRANT SELECT ON temporary_email_domains TO service_role;

-- Update existing users with temporary email domains to have subscription_status='bot'
-- Only update users who are currently in 'trial' status (don't override paid users)
WITH updated_users AS (
    UPDATE users
    SET
        subscription_status = 'bot',
        updated_at = NOW()
    WHERE subscription_status = 'trial'
        AND is_temporary_email(email)
    RETURNING id, email, subscription_status
)
SELECT
    COUNT(*) AS users_marked_as_bot,
    NOW() AS migration_timestamp
FROM updated_users;

-- Log the migration results
DO $$
DECLARE
    affected_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO affected_count
    FROM users
    WHERE subscription_status = 'bot';

    RAISE NOTICE 'Migration complete: % users now have subscription_status=bot', affected_count;
END $$;

-- ============================================================
-- Part 2: Expired Trial Handling
-- ============================================================

-- Create function to mark expired trials
CREATE OR REPLACE FUNCTION mark_expired_trials()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    users_expired INTEGER := 0;
    api_keys_expired INTEGER := 0;
    result jsonb;
BEGIN
    -- Update users table: mark trials as expired where trial_expires_at has passed
    -- Only update users who are still in 'trial' status (not paid, not bot)
    WITH updated_users AS (
        UPDATE users
        SET
            subscription_status = 'expired',
            updated_at = NOW()
        WHERE subscription_status = 'trial'
            AND trial_expires_at IS NOT NULL
            AND trial_expires_at < NOW()
        RETURNING id
    )
    SELECT COUNT(*) INTO users_expired FROM updated_users;

    -- Update api_keys_new table: mark trial keys as expired
    WITH updated_keys AS (
        UPDATE api_keys_new
        SET
            is_trial = FALSE,
            subscription_status = 'expired',
            updated_at = NOW()
        FROM users u
        WHERE api_keys_new.user_id = u.id
            AND api_keys_new.is_trial = TRUE
            AND u.trial_expires_at IS NOT NULL
            AND u.trial_expires_at < NOW()
        RETURNING api_keys_new.id
    )
    SELECT COUNT(*) INTO api_keys_expired FROM updated_keys;

    -- Build result JSON
    result := jsonb_build_object(
        'timestamp', NOW(),
        'users_expired', users_expired,
        'api_keys_expired', api_keys_expired
    );

    RAISE NOTICE 'Expired trials marked: %', result;
    RETURN result;
END;
$$;

-- Grant execute permission
GRANT EXECUTE ON FUNCTION mark_expired_trials() TO service_role;

-- Create wrapper function that logs results
CREATE OR REPLACE FUNCTION run_and_log_expired_trials()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    result jsonb;
BEGIN
    -- Run the expiration check
    result := mark_expired_trials();

    -- Log the result
    INSERT INTO reconciliation_logs (job_name, result)
    VALUES ('expired_trials_check', result);

    -- Only keep last 90 days of logs
    DELETE FROM reconciliation_logs
    WHERE job_name = 'expired_trials_check'
    AND created_at < NOW() - INTERVAL '90 days';
END;
$$;

-- Grant execute permission
GRANT EXECUTE ON FUNCTION run_and_log_expired_trials() TO service_role;

-- Schedule the job to run every hour
-- Note: pg_cron jobs are stored in the cron schema
DO $block$
BEGIN
    -- Check if pg_cron extension exists
    IF EXISTS (
        SELECT 1 FROM pg_extension WHERE extname = 'pg_cron'
    ) THEN
        -- Remove existing job if it exists
        PERFORM cron.unschedule('mark-expired-trials')
        WHERE EXISTS (
            SELECT 1 FROM cron.job WHERE jobname = 'mark-expired-trials'
        );

        -- Schedule the cron job to run hourly
        PERFORM cron.schedule(
            'mark-expired-trials',           -- job name
            '0 * * * *',                     -- cron schedule: every hour at minute 0
            $$SELECT run_and_log_expired_trials()$$
        );
        RAISE NOTICE 'Successfully scheduled expired trials cron job (hourly)';
    ELSE
        RAISE WARNING 'pg_cron extension not found. Expired trial marking will not run automatically.';
    END IF;
EXCEPTION
    WHEN OTHERS THEN
        RAISE WARNING 'Failed to schedule expired trials cron job: %. Job can be scheduled manually later.', SQLERRM;
END;
$block$;

-- Add comments
COMMENT ON FUNCTION is_temporary_email(TEXT) IS
'Checks if an email address is from a known temporary/disposable email domain.
Used to identify bot accounts during signup and for retrospective analysis.';

COMMENT ON FUNCTION mark_expired_trials() IS
'Marks trial accounts as expired when trial_expires_at has passed.
Updates both users and api_keys_new tables.
Scheduled to run hourly via pg_cron.';

COMMENT ON TABLE temporary_email_domains IS
'List of known temporary/disposable email domains used to identify bot accounts.
Synced from src/utils/security_validators.py TEMPORARY_EMAIL_DOMAINS.';

-- Run expired trials check immediately to catch any existing expired trials
SELECT run_and_log_expired_trials();
