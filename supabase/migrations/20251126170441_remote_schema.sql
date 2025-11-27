alter table "public"."admin_users" drop constraint "admin_users_role_check";

alter table "public"."admin_users" drop constraint "admin_users_status_check";

alter table "public"."chat_messages" drop constraint "chat_messages_role_check";

alter table "public"."admin_users" add constraint "admin_users_role_check" CHECK (((role)::text = ANY ((ARRAY['superadmin'::character varying, 'admin'::character varying, 'dev'::character varying])::text[]))) not valid;

alter table "public"."admin_users" validate constraint "admin_users_role_check";

alter table "public"."admin_users" add constraint "admin_users_status_check" CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[]))) not valid;

alter table "public"."admin_users" validate constraint "admin_users_status_check";

alter table "public"."chat_messages" add constraint "chat_messages_role_check" CHECK (((role)::text = ANY ((ARRAY['user'::character varying, 'assistant'::character varying])::text[]))) not valid;

alter table "public"."chat_messages" validate constraint "chat_messages_role_check";

set check_function_bodies = off;

CREATE OR REPLACE FUNCTION public.generate_referral_code()
 RETURNS text
 LANGUAGE plpgsql
AS $function$
DECLARE
    characters TEXT := 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    result TEXT := '';
    i INTEGER;
BEGIN
    FOR i IN 1..8 LOOP
        result := result || substr(characters, floor(random() * length(characters) + 1)::integer, 1);
    END LOOP;
    RETURN result;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.update_notifications_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.update_updated_at_column()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$function$
;


