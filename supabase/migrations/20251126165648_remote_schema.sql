drop extension if exists "pg_net";

-- Drop triggers only if tables exist
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'model_health_tracking') THEN
        DROP TRIGGER IF EXISTS "trigger_update_model_health_tracking_updated_at" ON "public"."model_health_tracking";
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'models') THEN
        DROP TRIGGER IF EXISTS "update_models_updated_at" ON "public"."models";
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'providers') THEN
        DROP TRIGGER IF EXISTS "update_providers_updated_at" ON "public"."providers";
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'subscription_products') THEN
        DROP TRIGGER IF EXISTS "trigger_update_subscription_products_updated_at" ON "public"."subscription_products";
    END IF;
END $$;

drop policy if exists "Service role can manage audit logs" on "public"."api_key_audit_logs";

drop policy if exists "Users can read their own audit logs" on "public"."api_key_audit_logs";

drop policy if exists "Allow authenticated read access to health history" on "public"."model_health_history";

drop policy if exists "Allow service role full access to health history" on "public"."model_health_history";

drop policy if exists "Authenticated users can read" on "public"."model_health_tracking";

drop policy if exists "Service role can do anything" on "public"."model_health_tracking";

drop policy if exists "Allow authenticated read access to models" on "public"."models";

drop policy if exists "Allow public read access to active models" on "public"."models";

drop policy if exists "Allow service role full access to models" on "public"."models";

drop policy if exists "Allow authenticated read access to providers" on "public"."providers";

drop policy if exists "Allow public read access to providers" on "public"."providers";

drop policy if exists "Allow service role full access to providers" on "public"."providers";

drop policy if exists "Service role can manage rate limit configs" on "public"."rate_limit_configs";

drop policy if exists "Users can read their own rate limit configs" on "public"."rate_limit_configs";

drop policy if exists "Service role can manage rate limit usage" on "public"."rate_limit_usage";

drop policy if exists "Users can read their own rate limit usage" on "public"."rate_limit_usage";

revoke delete on table "public"."api_key_audit_logs" from "anon";

revoke insert on table "public"."api_key_audit_logs" from "anon";

revoke references on table "public"."api_key_audit_logs" from "anon";

revoke select on table "public"."api_key_audit_logs" from "anon";

revoke trigger on table "public"."api_key_audit_logs" from "anon";

revoke truncate on table "public"."api_key_audit_logs" from "anon";

revoke update on table "public"."api_key_audit_logs" from "anon";

revoke delete on table "public"."api_key_audit_logs" from "authenticated";

revoke insert on table "public"."api_key_audit_logs" from "authenticated";

revoke references on table "public"."api_key_audit_logs" from "authenticated";

revoke select on table "public"."api_key_audit_logs" from "authenticated";

revoke trigger on table "public"."api_key_audit_logs" from "authenticated";

revoke truncate on table "public"."api_key_audit_logs" from "authenticated";

revoke update on table "public"."api_key_audit_logs" from "authenticated";

revoke delete on table "public"."api_key_audit_logs" from "service_role";

revoke insert on table "public"."api_key_audit_logs" from "service_role";

revoke references on table "public"."api_key_audit_logs" from "service_role";

revoke select on table "public"."api_key_audit_logs" from "service_role";

revoke trigger on table "public"."api_key_audit_logs" from "service_role";

revoke truncate on table "public"."api_key_audit_logs" from "service_role";

revoke update on table "public"."api_key_audit_logs" from "service_role";

revoke delete on table "public"."model_health_history" from "anon";

revoke insert on table "public"."model_health_history" from "anon";

revoke references on table "public"."model_health_history" from "anon";

revoke select on table "public"."model_health_history" from "anon";

revoke trigger on table "public"."model_health_history" from "anon";

revoke truncate on table "public"."model_health_history" from "anon";

revoke update on table "public"."model_health_history" from "anon";

revoke delete on table "public"."model_health_history" from "authenticated";

revoke insert on table "public"."model_health_history" from "authenticated";

revoke references on table "public"."model_health_history" from "authenticated";

revoke select on table "public"."model_health_history" from "authenticated";

revoke trigger on table "public"."model_health_history" from "authenticated";

revoke truncate on table "public"."model_health_history" from "authenticated";

revoke update on table "public"."model_health_history" from "authenticated";

revoke delete on table "public"."model_health_history" from "service_role";

revoke insert on table "public"."model_health_history" from "service_role";

revoke references on table "public"."model_health_history" from "service_role";

revoke select on table "public"."model_health_history" from "service_role";

revoke trigger on table "public"."model_health_history" from "service_role";

revoke truncate on table "public"."model_health_history" from "service_role";

revoke update on table "public"."model_health_history" from "service_role";

revoke delete on table "public"."model_health_tracking" from "anon";

revoke insert on table "public"."model_health_tracking" from "anon";

revoke references on table "public"."model_health_tracking" from "anon";

revoke select on table "public"."model_health_tracking" from "anon";

revoke trigger on table "public"."model_health_tracking" from "anon";

revoke truncate on table "public"."model_health_tracking" from "anon";

revoke update on table "public"."model_health_tracking" from "anon";

revoke delete on table "public"."model_health_tracking" from "authenticated";

revoke insert on table "public"."model_health_tracking" from "authenticated";

revoke references on table "public"."model_health_tracking" from "authenticated";

revoke select on table "public"."model_health_tracking" from "authenticated";

revoke trigger on table "public"."model_health_tracking" from "authenticated";

revoke truncate on table "public"."model_health_tracking" from "authenticated";

revoke update on table "public"."model_health_tracking" from "authenticated";

revoke delete on table "public"."model_health_tracking" from "service_role";

revoke insert on table "public"."model_health_tracking" from "service_role";

revoke references on table "public"."model_health_tracking" from "service_role";

revoke select on table "public"."model_health_tracking" from "service_role";

revoke trigger on table "public"."model_health_tracking" from "service_role";

revoke truncate on table "public"."model_health_tracking" from "service_role";

revoke update on table "public"."model_health_tracking" from "service_role";

revoke delete on table "public"."models" from "anon";

revoke insert on table "public"."models" from "anon";

revoke references on table "public"."models" from "anon";

revoke select on table "public"."models" from "anon";

revoke trigger on table "public"."models" from "anon";

revoke truncate on table "public"."models" from "anon";

revoke update on table "public"."models" from "anon";

revoke delete on table "public"."models" from "authenticated";

revoke insert on table "public"."models" from "authenticated";

revoke references on table "public"."models" from "authenticated";

revoke select on table "public"."models" from "authenticated";

revoke trigger on table "public"."models" from "authenticated";

revoke truncate on table "public"."models" from "authenticated";

revoke update on table "public"."models" from "authenticated";

revoke delete on table "public"."models" from "service_role";

revoke insert on table "public"."models" from "service_role";

revoke references on table "public"."models" from "service_role";

revoke select on table "public"."models" from "service_role";

revoke trigger on table "public"."models" from "service_role";

revoke truncate on table "public"."models" from "service_role";

revoke update on table "public"."models" from "service_role";

revoke delete on table "public"."providers" from "anon";

revoke insert on table "public"."providers" from "anon";

revoke references on table "public"."providers" from "anon";

revoke select on table "public"."providers" from "anon";

revoke trigger on table "public"."providers" from "anon";

revoke truncate on table "public"."providers" from "anon";

revoke update on table "public"."providers" from "anon";

revoke delete on table "public"."providers" from "authenticated";

revoke insert on table "public"."providers" from "authenticated";

revoke references on table "public"."providers" from "authenticated";

revoke select on table "public"."providers" from "authenticated";

revoke trigger on table "public"."providers" from "authenticated";

revoke truncate on table "public"."providers" from "authenticated";

revoke update on table "public"."providers" from "authenticated";

revoke delete on table "public"."providers" from "service_role";

revoke insert on table "public"."providers" from "service_role";

revoke references on table "public"."providers" from "service_role";

revoke select on table "public"."providers" from "service_role";

revoke trigger on table "public"."providers" from "service_role";

revoke truncate on table "public"."providers" from "service_role";

revoke update on table "public"."providers" from "service_role";

revoke delete on table "public"."rate_limit_configs" from "anon";

revoke insert on table "public"."rate_limit_configs" from "anon";

revoke references on table "public"."rate_limit_configs" from "anon";

revoke select on table "public"."rate_limit_configs" from "anon";

revoke trigger on table "public"."rate_limit_configs" from "anon";

revoke truncate on table "public"."rate_limit_configs" from "anon";

revoke update on table "public"."rate_limit_configs" from "anon";

revoke delete on table "public"."rate_limit_configs" from "authenticated";

revoke insert on table "public"."rate_limit_configs" from "authenticated";

revoke references on table "public"."rate_limit_configs" from "authenticated";

revoke select on table "public"."rate_limit_configs" from "authenticated";

revoke trigger on table "public"."rate_limit_configs" from "authenticated";

revoke truncate on table "public"."rate_limit_configs" from "authenticated";

revoke update on table "public"."rate_limit_configs" from "authenticated";

revoke delete on table "public"."rate_limit_configs" from "service_role";

revoke insert on table "public"."rate_limit_configs" from "service_role";

revoke references on table "public"."rate_limit_configs" from "service_role";

revoke select on table "public"."rate_limit_configs" from "service_role";

revoke trigger on table "public"."rate_limit_configs" from "service_role";

revoke truncate on table "public"."rate_limit_configs" from "service_role";

revoke update on table "public"."rate_limit_configs" from "service_role";

revoke delete on table "public"."rate_limit_usage" from "anon";

revoke insert on table "public"."rate_limit_usage" from "anon";

revoke references on table "public"."rate_limit_usage" from "anon";

revoke select on table "public"."rate_limit_usage" from "anon";

revoke trigger on table "public"."rate_limit_usage" from "anon";

revoke truncate on table "public"."rate_limit_usage" from "anon";

revoke update on table "public"."rate_limit_usage" from "anon";

revoke delete on table "public"."rate_limit_usage" from "authenticated";

revoke insert on table "public"."rate_limit_usage" from "authenticated";

revoke references on table "public"."rate_limit_usage" from "authenticated";

revoke select on table "public"."rate_limit_usage" from "authenticated";

revoke trigger on table "public"."rate_limit_usage" from "authenticated";

revoke truncate on table "public"."rate_limit_usage" from "authenticated";

revoke update on table "public"."rate_limit_usage" from "authenticated";

revoke delete on table "public"."rate_limit_usage" from "service_role";

revoke insert on table "public"."rate_limit_usage" from "service_role";

revoke references on table "public"."rate_limit_usage" from "service_role";

revoke select on table "public"."rate_limit_usage" from "service_role";

revoke trigger on table "public"."rate_limit_usage" from "service_role";

revoke truncate on table "public"."rate_limit_usage" from "service_role";

revoke update on table "public"."rate_limit_usage" from "service_role";

revoke delete on table "public"."stripe_webhook_events" from "anon";

revoke insert on table "public"."stripe_webhook_events" from "anon";

revoke references on table "public"."stripe_webhook_events" from "anon";

revoke select on table "public"."stripe_webhook_events" from "anon";

revoke trigger on table "public"."stripe_webhook_events" from "anon";

revoke truncate on table "public"."stripe_webhook_events" from "anon";

revoke update on table "public"."stripe_webhook_events" from "anon";

revoke delete on table "public"."stripe_webhook_events" from "authenticated";

revoke insert on table "public"."stripe_webhook_events" from "authenticated";

revoke references on table "public"."stripe_webhook_events" from "authenticated";

revoke select on table "public"."stripe_webhook_events" from "authenticated";

revoke trigger on table "public"."stripe_webhook_events" from "authenticated";

revoke truncate on table "public"."stripe_webhook_events" from "authenticated";

revoke update on table "public"."stripe_webhook_events" from "authenticated";

revoke delete on table "public"."stripe_webhook_events" from "service_role";

revoke insert on table "public"."stripe_webhook_events" from "service_role";

revoke references on table "public"."stripe_webhook_events" from "service_role";

revoke select on table "public"."stripe_webhook_events" from "service_role";

revoke trigger on table "public"."stripe_webhook_events" from "service_role";

revoke truncate on table "public"."stripe_webhook_events" from "service_role";

revoke update on table "public"."stripe_webhook_events" from "service_role";

revoke delete on table "public"."subscription_products" from "anon";

revoke insert on table "public"."subscription_products" from "anon";

revoke references on table "public"."subscription_products" from "anon";

revoke select on table "public"."subscription_products" from "anon";

revoke trigger on table "public"."subscription_products" from "anon";

revoke truncate on table "public"."subscription_products" from "anon";

revoke update on table "public"."subscription_products" from "anon";

revoke delete on table "public"."subscription_products" from "authenticated";

revoke insert on table "public"."subscription_products" from "authenticated";

revoke references on table "public"."subscription_products" from "authenticated";

revoke select on table "public"."subscription_products" from "authenticated";

revoke trigger on table "public"."subscription_products" from "authenticated";

revoke truncate on table "public"."subscription_products" from "authenticated";

revoke update on table "public"."subscription_products" from "authenticated";

revoke delete on table "public"."subscription_products" from "service_role";

revoke insert on table "public"."subscription_products" from "service_role";

revoke references on table "public"."subscription_products" from "service_role";

revoke select on table "public"."subscription_products" from "service_role";

revoke trigger on table "public"."subscription_products" from "service_role";

revoke truncate on table "public"."subscription_products" from "service_role";

revoke update on table "public"."subscription_products" from "service_role";

alter table "public"."api_key_audit_logs" drop constraint "api_key_audit_logs_api_key_id_fkey";

alter table "public"."api_key_audit_logs" drop constraint "api_key_audit_logs_user_id_fkey";

alter table "public"."model_health_history" drop constraint "model_health_history_health_status_check";

alter table "public"."model_health_history" drop constraint "model_health_history_model_id_fkey";

alter table "public"."models" drop constraint "models_health_status_check";

alter table "public"."models" drop constraint "models_provider_id_fkey";

alter table "public"."models" drop constraint "unique_provider_model";

alter table "public"."providers" drop constraint "providers_health_status_check";

alter table "public"."providers" drop constraint "providers_name_key";

alter table "public"."providers" drop constraint "providers_slug_key";

alter table "public"."rate_limit_configs" drop constraint "rate_limit_configs_api_key_id_fkey";

alter table "public"."rate_limit_usage" drop constraint "rate_limit_usage_unique";

alter table "public"."rate_limit_usage" drop constraint "rate_limit_usage_user_id_fkey";

alter table "public"."chat_messages" drop constraint "chat_messages_role_check";

drop function if exists "public"."cleanup_old_webhook_events"();

drop function if exists "public"."get_credits_from_tier"(p_tier character varying);

drop function if exists "public"."get_tier_from_product"(p_product_id character varying);

-- Drop event trigger before dropping the function it depends on
drop event trigger if exists "postgrest_schema_reload";

drop function if exists "public"."notify_postgrest_schema_reload"();

drop function if exists "public"."refresh_postgrest_schema_cache"();

drop function if exists "public"."update_model_health_tracking_updated_at"();

drop function if exists "public"."update_subscription_products_updated_at"();

alter table "public"."api_key_audit_logs" drop constraint "api_key_audit_logs_pkey";

alter table "public"."model_health_history" drop constraint "model_health_history_pkey";

alter table "public"."model_health_tracking" drop constraint "model_health_tracking_pkey";

alter table "public"."models" drop constraint "models_pkey";

alter table "public"."providers" drop constraint "providers_pkey";

alter table "public"."rate_limit_configs" drop constraint "rate_limit_configs_pkey";

alter table "public"."rate_limit_usage" drop constraint "rate_limit_usage_pkey";

alter table "public"."stripe_webhook_events" drop constraint "stripe_webhook_events_pkey";

alter table "public"."subscription_products" drop constraint "subscription_products_pkey";

drop index if exists "public"."api_key_audit_logs_action_idx";

drop index if exists "public"."api_key_audit_logs_api_key_id_idx";

drop index if exists "public"."api_key_audit_logs_pkey";

drop index if exists "public"."api_key_audit_logs_timestamp_idx";

drop index if exists "public"."api_key_audit_logs_user_id_idx";

drop index if exists "public"."idx_api_keys_new_active";

drop index if exists "public"."idx_api_keys_new_api_key";

drop index if exists "public"."idx_api_keys_new_created_at";

drop index if exists "public"."idx_api_keys_new_environment";

drop index if exists "public"."idx_api_keys_new_is_primary";

drop index if exists "public"."idx_api_keys_new_primary";

drop index if exists "public"."idx_api_keys_new_user_id";

drop index if exists "public"."idx_chat_sessions_active";

drop index if exists "public"."idx_model_health_history_checked_at";

drop index if exists "public"."idx_model_health_history_model_id";

drop index if exists "public"."idx_model_health_last_called";

drop index if exists "public"."idx_model_health_provider";

drop index if exists "public"."idx_model_health_status";

drop index if exists "public"."idx_models_health_status";

drop index if exists "public"."idx_models_is_active";

drop index if exists "public"."idx_models_modality";

drop index if exists "public"."idx_models_model_id";

drop index if exists "public"."idx_models_provider_active";

drop index if exists "public"."idx_models_provider_id";

drop index if exists "public"."idx_models_provider_model_id";

drop index if exists "public"."idx_providers_health_status";

drop index if exists "public"."idx_providers_is_active";

drop index if exists "public"."idx_providers_slug";

drop index if exists "public"."idx_subscription_products_is_active";

drop index if exists "public"."idx_subscription_products_tier";

drop index if exists "public"."idx_users_active";

drop index if exists "public"."idx_users_id";

drop index if exists "public"."idx_users_privy_id";

drop index if exists "public"."idx_users_username";

drop index if exists "public"."idx_webhook_events_created_at";

drop index if exists "public"."idx_webhook_events_event_type";

drop index if exists "public"."idx_webhook_events_user_id";

drop index if exists "public"."model_health_history_pkey";

drop index if exists "public"."model_health_tracking_pkey";

drop index if exists "public"."models_pkey";

drop index if exists "public"."providers_name_key";

drop index if exists "public"."providers_pkey";

drop index if exists "public"."providers_slug_key";

drop index if exists "public"."rate_limit_configs_api_key_id_idx";

drop index if exists "public"."rate_limit_configs_pkey";

drop index if exists "public"."rate_limit_usage_api_key_idx";

drop index if exists "public"."rate_limit_usage_pkey";

drop index if exists "public"."rate_limit_usage_unique";

drop index if exists "public"."rate_limit_usage_user_id_idx";

drop index if exists "public"."rate_limit_usage_window_start_idx";

drop index if exists "public"."rate_limit_usage_window_type_idx";

drop index if exists "public"."stripe_webhook_events_pkey";

drop index if exists "public"."subscription_products_pkey";

drop index if exists "public"."unique_provider_model";

drop table "public"."api_key_audit_logs";

drop table "public"."model_health_history";

drop table "public"."model_health_tracking";

drop table "public"."models";

drop table "public"."providers";

drop table "public"."rate_limit_configs";

drop table "public"."rate_limit_usage";

drop table "public"."stripe_webhook_events";

drop table "public"."subscription_products";


  create table "public"."admin_users" (
    "id" uuid not null default gen_random_uuid(),
    "email" character varying(255) not null,
    "password" character varying(255) not null,
    "role" character varying(20) not null,
    "status" character varying(20) default 'active'::character varying,
    "created_at" timestamp with time zone default now(),
    "updated_at" timestamp with time zone default now(),
    "created_by" uuid,
    "last_login" timestamp with time zone
      );


alter table "public"."admin_users" enable row level security;

alter table "public"."users" drop column "balance";

alter table "public"."users" alter column "credits" set default 0.0;

alter table "public"."users" alter column "credits" set not null;

drop sequence if exists "public"."api_key_audit_logs_id_seq";

drop sequence if exists "public"."model_health_history_id_seq";

drop sequence if exists "public"."models_id_seq";

drop sequence if exists "public"."providers_id_seq";

drop sequence if exists "public"."rate_limit_configs_id_seq";

drop sequence if exists "public"."rate_limit_usage_id_seq";

CREATE UNIQUE INDEX admin_users_email_key ON public.admin_users USING btree (email);

CREATE UNIQUE INDEX admin_users_pkey ON public.admin_users USING btree (id);

CREATE INDEX idx_admin_users_created_at ON public.admin_users USING btree (created_at);

CREATE INDEX idx_admin_users_email ON public.admin_users USING btree (email);

CREATE INDEX idx_admin_users_role ON public.admin_users USING btree (role);

CREATE INDEX idx_admin_users_status ON public.admin_users USING btree (status);

CREATE INDEX idx_users_credits ON public.users USING btree (credits);

alter table "public"."admin_users" add constraint "admin_users_pkey" PRIMARY KEY using index "admin_users_pkey";

alter table "public"."admin_users" add constraint "admin_users_created_by_fkey" FOREIGN KEY (created_by) REFERENCES public.admin_users(id) not valid;

alter table "public"."admin_users" validate constraint "admin_users_created_by_fkey";

alter table "public"."admin_users" add constraint "admin_users_email_key" UNIQUE using index "admin_users_email_key";

alter table "public"."admin_users" add constraint "admin_users_role_check" CHECK (((role)::text = ANY ((ARRAY['superadmin'::character varying, 'admin'::character varying, 'dev'::character varying])::text[]))) not valid;

alter table "public"."admin_users" validate constraint "admin_users_role_check";

alter table "public"."admin_users" add constraint "admin_users_status_check" CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[]))) not valid;

alter table "public"."admin_users" validate constraint "admin_users_status_check";

alter table "public"."users" add constraint "users_credits_non_negative" CHECK ((credits >= (0)::numeric)) not valid;

alter table "public"."users" validate constraint "users_credits_non_negative";

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

grant delete on table "public"."activity_log" to "anon";

grant insert on table "public"."activity_log" to "anon";

grant references on table "public"."activity_log" to "anon";

grant select on table "public"."activity_log" to "anon";

grant trigger on table "public"."activity_log" to "anon";

grant truncate on table "public"."activity_log" to "anon";

grant update on table "public"."activity_log" to "anon";

grant delete on table "public"."activity_log" to "authenticated";

grant insert on table "public"."activity_log" to "authenticated";

grant references on table "public"."activity_log" to "authenticated";

grant select on table "public"."activity_log" to "authenticated";

grant trigger on table "public"."activity_log" to "authenticated";

grant truncate on table "public"."activity_log" to "authenticated";

grant update on table "public"."activity_log" to "authenticated";

grant delete on table "public"."activity_log" to "service_role";

grant insert on table "public"."activity_log" to "service_role";

grant references on table "public"."activity_log" to "service_role";

grant select on table "public"."activity_log" to "service_role";

grant trigger on table "public"."activity_log" to "service_role";

grant truncate on table "public"."activity_log" to "service_role";

grant update on table "public"."activity_log" to "service_role";

grant delete on table "public"."admin_users" to "anon";

grant insert on table "public"."admin_users" to "anon";

grant references on table "public"."admin_users" to "anon";

grant select on table "public"."admin_users" to "anon";

grant trigger on table "public"."admin_users" to "anon";

grant truncate on table "public"."admin_users" to "anon";

grant update on table "public"."admin_users" to "anon";

grant delete on table "public"."admin_users" to "authenticated";

grant insert on table "public"."admin_users" to "authenticated";

grant references on table "public"."admin_users" to "authenticated";

grant select on table "public"."admin_users" to "authenticated";

grant trigger on table "public"."admin_users" to "authenticated";

grant truncate on table "public"."admin_users" to "authenticated";

grant update on table "public"."admin_users" to "authenticated";

grant delete on table "public"."admin_users" to "service_role";

grant insert on table "public"."admin_users" to "service_role";

grant references on table "public"."admin_users" to "service_role";

grant select on table "public"."admin_users" to "service_role";

grant trigger on table "public"."admin_users" to "service_role";

grant truncate on table "public"."admin_users" to "service_role";

grant update on table "public"."admin_users" to "service_role";

grant delete on table "public"."api_keys_new" to "anon";

grant insert on table "public"."api_keys_new" to "anon";

grant references on table "public"."api_keys_new" to "anon";

grant select on table "public"."api_keys_new" to "anon";

grant trigger on table "public"."api_keys_new" to "anon";

grant truncate on table "public"."api_keys_new" to "anon";

grant update on table "public"."api_keys_new" to "anon";

grant delete on table "public"."api_keys_new" to "authenticated";

grant insert on table "public"."api_keys_new" to "authenticated";

grant references on table "public"."api_keys_new" to "authenticated";

grant select on table "public"."api_keys_new" to "authenticated";

grant trigger on table "public"."api_keys_new" to "authenticated";

grant truncate on table "public"."api_keys_new" to "authenticated";

grant update on table "public"."api_keys_new" to "authenticated";

grant delete on table "public"."api_keys_new" to "service_role";

grant insert on table "public"."api_keys_new" to "service_role";

grant references on table "public"."api_keys_new" to "service_role";

grant select on table "public"."api_keys_new" to "service_role";

grant trigger on table "public"."api_keys_new" to "service_role";

grant truncate on table "public"."api_keys_new" to "service_role";

grant update on table "public"."api_keys_new" to "service_role";

grant delete on table "public"."chat_messages" to "anon";

grant insert on table "public"."chat_messages" to "anon";

grant references on table "public"."chat_messages" to "anon";

grant select on table "public"."chat_messages" to "anon";

grant trigger on table "public"."chat_messages" to "anon";

grant truncate on table "public"."chat_messages" to "anon";

grant update on table "public"."chat_messages" to "anon";

grant delete on table "public"."chat_messages" to "authenticated";

grant insert on table "public"."chat_messages" to "authenticated";

grant references on table "public"."chat_messages" to "authenticated";

grant select on table "public"."chat_messages" to "authenticated";

grant trigger on table "public"."chat_messages" to "authenticated";

grant truncate on table "public"."chat_messages" to "authenticated";

grant update on table "public"."chat_messages" to "authenticated";

grant delete on table "public"."chat_messages" to "service_role";

grant insert on table "public"."chat_messages" to "service_role";

grant references on table "public"."chat_messages" to "service_role";

grant select on table "public"."chat_messages" to "service_role";

grant trigger on table "public"."chat_messages" to "service_role";

grant truncate on table "public"."chat_messages" to "service_role";

grant update on table "public"."chat_messages" to "service_role";

grant delete on table "public"."chat_sessions" to "anon";

grant insert on table "public"."chat_sessions" to "anon";

grant references on table "public"."chat_sessions" to "anon";

grant select on table "public"."chat_sessions" to "anon";

grant trigger on table "public"."chat_sessions" to "anon";

grant truncate on table "public"."chat_sessions" to "anon";

grant update on table "public"."chat_sessions" to "anon";

grant delete on table "public"."chat_sessions" to "authenticated";

grant insert on table "public"."chat_sessions" to "authenticated";

grant references on table "public"."chat_sessions" to "authenticated";

grant select on table "public"."chat_sessions" to "authenticated";

grant trigger on table "public"."chat_sessions" to "authenticated";

grant truncate on table "public"."chat_sessions" to "authenticated";

grant update on table "public"."chat_sessions" to "authenticated";

grant delete on table "public"."chat_sessions" to "service_role";

grant insert on table "public"."chat_sessions" to "service_role";

grant references on table "public"."chat_sessions" to "service_role";

grant select on table "public"."chat_sessions" to "service_role";

grant trigger on table "public"."chat_sessions" to "service_role";

grant truncate on table "public"."chat_sessions" to "service_role";

grant update on table "public"."chat_sessions" to "service_role";

grant delete on table "public"."coupon_redemptions" to "anon";

grant insert on table "public"."coupon_redemptions" to "anon";

grant references on table "public"."coupon_redemptions" to "anon";

grant select on table "public"."coupon_redemptions" to "anon";

grant trigger on table "public"."coupon_redemptions" to "anon";

grant truncate on table "public"."coupon_redemptions" to "anon";

grant update on table "public"."coupon_redemptions" to "anon";

grant delete on table "public"."coupon_redemptions" to "authenticated";

grant insert on table "public"."coupon_redemptions" to "authenticated";

grant references on table "public"."coupon_redemptions" to "authenticated";

grant select on table "public"."coupon_redemptions" to "authenticated";

grant trigger on table "public"."coupon_redemptions" to "authenticated";

grant truncate on table "public"."coupon_redemptions" to "authenticated";

grant update on table "public"."coupon_redemptions" to "authenticated";

grant delete on table "public"."coupon_redemptions" to "service_role";

grant insert on table "public"."coupon_redemptions" to "service_role";

grant references on table "public"."coupon_redemptions" to "service_role";

grant select on table "public"."coupon_redemptions" to "service_role";

grant trigger on table "public"."coupon_redemptions" to "service_role";

grant truncate on table "public"."coupon_redemptions" to "service_role";

grant update on table "public"."coupon_redemptions" to "service_role";

grant delete on table "public"."coupons" to "anon";

grant insert on table "public"."coupons" to "anon";

grant references on table "public"."coupons" to "anon";

grant select on table "public"."coupons" to "anon";

grant trigger on table "public"."coupons" to "anon";

grant truncate on table "public"."coupons" to "anon";

grant update on table "public"."coupons" to "anon";

grant delete on table "public"."coupons" to "authenticated";

grant insert on table "public"."coupons" to "authenticated";

grant references on table "public"."coupons" to "authenticated";

grant select on table "public"."coupons" to "authenticated";

grant trigger on table "public"."coupons" to "authenticated";

grant truncate on table "public"."coupons" to "authenticated";

grant update on table "public"."coupons" to "authenticated";

grant delete on table "public"."coupons" to "service_role";

grant insert on table "public"."coupons" to "service_role";

grant references on table "public"."coupons" to "service_role";

grant select on table "public"."coupons" to "service_role";

grant trigger on table "public"."coupons" to "service_role";

grant truncate on table "public"."coupons" to "service_role";

grant update on table "public"."coupons" to "service_role";

grant delete on table "public"."credit_transactions" to "anon";

grant insert on table "public"."credit_transactions" to "anon";

grant references on table "public"."credit_transactions" to "anon";

grant select on table "public"."credit_transactions" to "anon";

grant trigger on table "public"."credit_transactions" to "anon";

grant truncate on table "public"."credit_transactions" to "anon";

grant update on table "public"."credit_transactions" to "anon";

grant delete on table "public"."credit_transactions" to "authenticated";

grant insert on table "public"."credit_transactions" to "authenticated";

grant references on table "public"."credit_transactions" to "authenticated";

grant select on table "public"."credit_transactions" to "authenticated";

grant trigger on table "public"."credit_transactions" to "authenticated";

grant truncate on table "public"."credit_transactions" to "authenticated";

grant update on table "public"."credit_transactions" to "authenticated";

grant delete on table "public"."credit_transactions" to "service_role";

grant insert on table "public"."credit_transactions" to "service_role";

grant references on table "public"."credit_transactions" to "service_role";

grant select on table "public"."credit_transactions" to "service_role";

grant trigger on table "public"."credit_transactions" to "service_role";

grant truncate on table "public"."credit_transactions" to "service_role";

grant update on table "public"."credit_transactions" to "service_role";

grant delete on table "public"."openrouter_apps" to "anon";

grant insert on table "public"."openrouter_apps" to "anon";

grant references on table "public"."openrouter_apps" to "anon";

grant select on table "public"."openrouter_apps" to "anon";

grant trigger on table "public"."openrouter_apps" to "anon";

grant truncate on table "public"."openrouter_apps" to "anon";

grant update on table "public"."openrouter_apps" to "anon";

grant delete on table "public"."openrouter_apps" to "authenticated";

grant insert on table "public"."openrouter_apps" to "authenticated";

grant references on table "public"."openrouter_apps" to "authenticated";

grant select on table "public"."openrouter_apps" to "authenticated";

grant trigger on table "public"."openrouter_apps" to "authenticated";

grant truncate on table "public"."openrouter_apps" to "authenticated";

grant update on table "public"."openrouter_apps" to "authenticated";

grant delete on table "public"."openrouter_apps" to "service_role";

grant insert on table "public"."openrouter_apps" to "service_role";

grant references on table "public"."openrouter_apps" to "service_role";

grant select on table "public"."openrouter_apps" to "service_role";

grant trigger on table "public"."openrouter_apps" to "service_role";

grant truncate on table "public"."openrouter_apps" to "service_role";

grant update on table "public"."openrouter_apps" to "service_role";

grant delete on table "public"."openrouter_models" to "anon";

grant insert on table "public"."openrouter_models" to "anon";

grant references on table "public"."openrouter_models" to "anon";

grant select on table "public"."openrouter_models" to "anon";

grant trigger on table "public"."openrouter_models" to "anon";

grant truncate on table "public"."openrouter_models" to "anon";

grant update on table "public"."openrouter_models" to "anon";

grant delete on table "public"."openrouter_models" to "authenticated";

grant insert on table "public"."openrouter_models" to "authenticated";

grant references on table "public"."openrouter_models" to "authenticated";

grant select on table "public"."openrouter_models" to "authenticated";

grant trigger on table "public"."openrouter_models" to "authenticated";

grant truncate on table "public"."openrouter_models" to "authenticated";

grant update on table "public"."openrouter_models" to "authenticated";

grant delete on table "public"."openrouter_models" to "service_role";

grant insert on table "public"."openrouter_models" to "service_role";

grant references on table "public"."openrouter_models" to "service_role";

grant select on table "public"."openrouter_models" to "service_role";

grant trigger on table "public"."openrouter_models" to "service_role";

grant truncate on table "public"."openrouter_models" to "service_role";

grant update on table "public"."openrouter_models" to "service_role";

grant delete on table "public"."payments" to "anon";

grant insert on table "public"."payments" to "anon";

grant references on table "public"."payments" to "anon";

grant select on table "public"."payments" to "anon";

grant trigger on table "public"."payments" to "anon";

grant truncate on table "public"."payments" to "anon";

grant update on table "public"."payments" to "anon";

grant delete on table "public"."payments" to "authenticated";

grant insert on table "public"."payments" to "authenticated";

grant references on table "public"."payments" to "authenticated";

grant select on table "public"."payments" to "authenticated";

grant trigger on table "public"."payments" to "authenticated";

grant truncate on table "public"."payments" to "authenticated";

grant update on table "public"."payments" to "authenticated";

grant delete on table "public"."payments" to "service_role";

grant insert on table "public"."payments" to "service_role";

grant references on table "public"."payments" to "service_role";

grant select on table "public"."payments" to "service_role";

grant trigger on table "public"."payments" to "service_role";

grant truncate on table "public"."payments" to "service_role";

grant update on table "public"."payments" to "service_role";

grant delete on table "public"."plans" to "anon";

grant insert on table "public"."plans" to "anon";

grant references on table "public"."plans" to "anon";

grant select on table "public"."plans" to "anon";

grant trigger on table "public"."plans" to "anon";

grant truncate on table "public"."plans" to "anon";

grant update on table "public"."plans" to "anon";

grant delete on table "public"."plans" to "authenticated";

grant insert on table "public"."plans" to "authenticated";

grant references on table "public"."plans" to "authenticated";

grant select on table "public"."plans" to "authenticated";

grant trigger on table "public"."plans" to "authenticated";

grant truncate on table "public"."plans" to "authenticated";

grant update on table "public"."plans" to "authenticated";

grant delete on table "public"."plans" to "service_role";

grant insert on table "public"."plans" to "service_role";

grant references on table "public"."plans" to "service_role";

grant select on table "public"."plans" to "service_role";

grant trigger on table "public"."plans" to "service_role";

grant truncate on table "public"."plans" to "service_role";

grant update on table "public"."plans" to "service_role";

grant delete on table "public"."pricing_tiers" to "anon";

grant insert on table "public"."pricing_tiers" to "anon";

grant references on table "public"."pricing_tiers" to "anon";

grant select on table "public"."pricing_tiers" to "anon";

grant trigger on table "public"."pricing_tiers" to "anon";

grant truncate on table "public"."pricing_tiers" to "anon";

grant update on table "public"."pricing_tiers" to "anon";

grant delete on table "public"."pricing_tiers" to "authenticated";

grant insert on table "public"."pricing_tiers" to "authenticated";

grant references on table "public"."pricing_tiers" to "authenticated";

grant select on table "public"."pricing_tiers" to "authenticated";

grant trigger on table "public"."pricing_tiers" to "authenticated";

grant truncate on table "public"."pricing_tiers" to "authenticated";

grant update on table "public"."pricing_tiers" to "authenticated";

grant delete on table "public"."pricing_tiers" to "service_role";

grant insert on table "public"."pricing_tiers" to "service_role";

grant references on table "public"."pricing_tiers" to "service_role";

grant select on table "public"."pricing_tiers" to "service_role";

grant trigger on table "public"."pricing_tiers" to "service_role";

grant truncate on table "public"."pricing_tiers" to "service_role";

grant update on table "public"."pricing_tiers" to "service_role";

grant delete on table "public"."role_permissions" to "anon";

grant insert on table "public"."role_permissions" to "anon";

grant references on table "public"."role_permissions" to "anon";

grant select on table "public"."role_permissions" to "anon";

grant trigger on table "public"."role_permissions" to "anon";

grant truncate on table "public"."role_permissions" to "anon";

grant update on table "public"."role_permissions" to "anon";

grant delete on table "public"."role_permissions" to "authenticated";

grant insert on table "public"."role_permissions" to "authenticated";

grant references on table "public"."role_permissions" to "authenticated";

grant select on table "public"."role_permissions" to "authenticated";

grant trigger on table "public"."role_permissions" to "authenticated";

grant truncate on table "public"."role_permissions" to "authenticated";

grant update on table "public"."role_permissions" to "authenticated";

grant delete on table "public"."role_permissions" to "service_role";

grant insert on table "public"."role_permissions" to "service_role";

grant references on table "public"."role_permissions" to "service_role";

grant select on table "public"."role_permissions" to "service_role";

grant trigger on table "public"."role_permissions" to "service_role";

grant truncate on table "public"."role_permissions" to "service_role";

grant update on table "public"."role_permissions" to "service_role";

grant delete on table "public"."trial_config" to "anon";

grant insert on table "public"."trial_config" to "anon";

grant references on table "public"."trial_config" to "anon";

grant select on table "public"."trial_config" to "anon";

grant trigger on table "public"."trial_config" to "anon";

grant truncate on table "public"."trial_config" to "anon";

grant update on table "public"."trial_config" to "anon";

grant delete on table "public"."trial_config" to "authenticated";

grant insert on table "public"."trial_config" to "authenticated";

grant references on table "public"."trial_config" to "authenticated";

grant select on table "public"."trial_config" to "authenticated";

grant trigger on table "public"."trial_config" to "authenticated";

grant truncate on table "public"."trial_config" to "authenticated";

grant update on table "public"."trial_config" to "authenticated";

grant delete on table "public"."trial_config" to "service_role";

grant insert on table "public"."trial_config" to "service_role";

grant references on table "public"."trial_config" to "service_role";

grant select on table "public"."trial_config" to "service_role";

grant trigger on table "public"."trial_config" to "service_role";

grant truncate on table "public"."trial_config" to "service_role";

grant update on table "public"."trial_config" to "service_role";

grant delete on table "public"."usage_records" to "anon";

grant insert on table "public"."usage_records" to "anon";

grant references on table "public"."usage_records" to "anon";

grant select on table "public"."usage_records" to "anon";

grant trigger on table "public"."usage_records" to "anon";

grant truncate on table "public"."usage_records" to "anon";

grant update on table "public"."usage_records" to "anon";

grant delete on table "public"."usage_records" to "authenticated";

grant insert on table "public"."usage_records" to "authenticated";

grant references on table "public"."usage_records" to "authenticated";

grant select on table "public"."usage_records" to "authenticated";

grant trigger on table "public"."usage_records" to "authenticated";

grant truncate on table "public"."usage_records" to "authenticated";

grant update on table "public"."usage_records" to "authenticated";

grant delete on table "public"."usage_records" to "service_role";

grant insert on table "public"."usage_records" to "service_role";

grant references on table "public"."usage_records" to "service_role";

grant select on table "public"."usage_records" to "service_role";

grant trigger on table "public"."usage_records" to "service_role";

grant truncate on table "public"."usage_records" to "service_role";

grant update on table "public"."usage_records" to "service_role";

grant delete on table "public"."user_plans" to "anon";

grant insert on table "public"."user_plans" to "anon";

grant references on table "public"."user_plans" to "anon";

grant select on table "public"."user_plans" to "anon";

grant trigger on table "public"."user_plans" to "anon";

grant truncate on table "public"."user_plans" to "anon";

grant update on table "public"."user_plans" to "anon";

grant delete on table "public"."user_plans" to "authenticated";

grant insert on table "public"."user_plans" to "authenticated";

grant references on table "public"."user_plans" to "authenticated";

grant select on table "public"."user_plans" to "authenticated";

grant trigger on table "public"."user_plans" to "authenticated";

grant truncate on table "public"."user_plans" to "authenticated";

grant update on table "public"."user_plans" to "authenticated";

grant delete on table "public"."user_plans" to "service_role";

grant insert on table "public"."user_plans" to "service_role";

grant references on table "public"."user_plans" to "service_role";

grant select on table "public"."user_plans" to "service_role";

grant trigger on table "public"."user_plans" to "service_role";

grant truncate on table "public"."user_plans" to "service_role";

grant update on table "public"."user_plans" to "service_role";

grant delete on table "public"."users" to "anon";

grant insert on table "public"."users" to "anon";

grant references on table "public"."users" to "anon";

grant select on table "public"."users" to "anon";

grant trigger on table "public"."users" to "anon";

grant truncate on table "public"."users" to "anon";

grant update on table "public"."users" to "anon";

grant delete on table "public"."users" to "authenticated";

grant insert on table "public"."users" to "authenticated";

grant references on table "public"."users" to "authenticated";

grant select on table "public"."users" to "authenticated";

grant trigger on table "public"."users" to "authenticated";

grant truncate on table "public"."users" to "authenticated";

grant update on table "public"."users" to "authenticated";

grant delete on table "public"."users" to "service_role";

grant insert on table "public"."users" to "service_role";

grant references on table "public"."users" to "service_role";

grant select on table "public"."users" to "service_role";

grant trigger on table "public"."users" to "service_role";

grant truncate on table "public"."users" to "service_role";

grant update on table "public"."users" to "service_role";


  create policy "Superadmins can create admin users"
  on "public"."admin_users"
  as permissive
  for insert
  to public
with check ((EXISTS ( SELECT 1
   FROM public.admin_users admin_users_1
  WHERE (((admin_users_1.email)::text = (auth.jwt() ->> 'email'::text)) AND ((admin_users_1.role)::text = 'superadmin'::text) AND ((admin_users_1.status)::text = 'active'::text)))));



  create policy "Superadmins can delete admin users"
  on "public"."admin_users"
  as permissive
  for delete
  to public
using ((EXISTS ( SELECT 1
   FROM public.admin_users admin_users_1
  WHERE (((admin_users_1.email)::text = (auth.jwt() ->> 'email'::text)) AND ((admin_users_1.role)::text = 'superadmin'::text) AND ((admin_users_1.status)::text = 'active'::text)))));



  create policy "Superadmins can update admin users"
  on "public"."admin_users"
  as permissive
  for update
  to public
using ((EXISTS ( SELECT 1
   FROM public.admin_users admin_users_1
  WHERE (((admin_users_1.email)::text = (auth.jwt() ->> 'email'::text)) AND ((admin_users_1.role)::text = 'superadmin'::text) AND ((admin_users_1.status)::text = 'active'::text)))));



  create policy "Superadmins can view all admin users"
  on "public"."admin_users"
  as permissive
  for select
  to public
using ((EXISTS ( SELECT 1
   FROM public.admin_users admin_users_1
  WHERE (((admin_users_1.email)::text = (auth.jwt() ->> 'email'::text)) AND ((admin_users_1.role)::text = 'superadmin'::text) AND ((admin_users_1.status)::text = 'active'::text)))));



  create policy "Service role has full access"
  on "public"."api_keys_new"
  as permissive
  for all
  to service_role
using (true)
with check (true);



  create policy "Service role has full access"
  on "public"."credit_transactions"
  as permissive
  for all
  to service_role
using (true)
with check (true);



  create policy "Service role has full access"
  on "public"."referrals"
  as permissive
  for all
  to service_role
using (true)
with check (true);



  create policy "Service role has full access"
  on "public"."users"
  as permissive
  for all
  to service_role
using (true)
with check (true);


CREATE TRIGGER update_admin_users_updated_at BEFORE UPDATE ON public.admin_users FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


