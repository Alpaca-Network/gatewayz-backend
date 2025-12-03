alter table "public"."admin_users" drop constraint "admin_users_role_check";

alter table "public"."admin_users" drop constraint "admin_users_status_check";

alter table "public"."chat_messages" drop constraint "chat_messages_role_check";

alter table "public"."admin_users" add constraint "admin_users_role_check" CHECK (((role)::text = ANY ((ARRAY['superadmin'::character varying, 'admin'::character varying, 'dev'::character varying])::text[]))) not valid;

alter table "public"."admin_users" validate constraint "admin_users_role_check";

alter table "public"."admin_users" add constraint "admin_users_status_check" CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[]))) not valid;

alter table "public"."admin_users" validate constraint "admin_users_status_check";

alter table "public"."chat_messages" add constraint "chat_messages_role_check" CHECK (((role)::text = ANY ((ARRAY['user'::character varying, 'assistant'::character varying])::text[]))) not valid;

alter table "public"."chat_messages" validate constraint "chat_messages_role_check";


