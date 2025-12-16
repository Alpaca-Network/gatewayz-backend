drop trigger if exists "update_admin_users_updated_at" on "public"."admin_users";

drop trigger if exists "update_api_keys_updated_at" on "public"."api_keys_new";

drop trigger if exists "trg_assign_chat_message_sequence" on "public"."chat_messages";

drop trigger if exists "update_coupons_updated_at" on "public"."coupons";

drop trigger if exists "trigger_update_message_feedback_updated_at" on "public"."message_feedback";

drop trigger if exists "trigger_update_metrics_hourly_updated_at" on "public"."metrics_hourly_aggregates";

drop trigger if exists "trigger_update_incident_duration" on "public"."model_health_incidents";

drop trigger if exists "trigger_update_model_health_incidents_updated_at" on "public"."model_health_incidents";

drop trigger if exists "trigger_update_model_health_tracking_updated_at" on "public"."model_health_tracking";

drop trigger if exists "update_models_updated_at" on "public"."models";

drop trigger if exists "trigger_update_notification_preferences_updated_at" on "public"."notification_preferences";

drop trigger if exists "trigger_update_notifications_updated_at" on "public"."notifications";

drop trigger if exists "update_payments_updated_at" on "public"."payments";

drop trigger if exists "update_providers_updated_at" on "public"."providers";

drop trigger if exists "update_users_updated_at" on "public"."users";

drop policy "Service role can insert activity" on "public"."activity_log";

drop policy "Users can view own activity" on "public"."activity_log";

drop policy "Superadmins can create admin users" on "public"."admin_users";

drop policy "Superadmins can delete admin users" on "public"."admin_users";

drop policy "Superadmins can update admin users" on "public"."admin_users";

drop policy "Superadmins can view all admin users" on "public"."admin_users";

drop policy "Service role has full access to api_keys" on "public"."api_keys_new";

drop policy "Service role has full access" on "public"."api_keys_new";

drop policy "Users can view their own API keys" on "public"."api_keys_new";

drop policy "Service role has full access to redemptions" on "public"."coupon_redemptions";

drop policy "Users can view redemptions" on "public"."coupon_redemptions";

drop policy "Authenticated users can view active coupons" on "public"."coupons";

drop policy "Service role has full access to coupons" on "public"."coupons";

drop policy "Service role has full access" on "public"."credit_transactions";

drop policy "anon_can_insert" on "public"."credit_transactions";

drop policy "service_role_all" on "public"."credit_transactions";

drop policy "users_can_view_own_transactions" on "public"."credit_transactions";

drop policy "Service role can manage all feedback" on "public"."message_feedback";

drop policy "Users can delete their own feedback" on "public"."message_feedback";

drop policy "Users can insert their own feedback" on "public"."message_feedback";

drop policy "Users can update their own feedback" on "public"."message_feedback";

drop policy "Users can view their own feedback" on "public"."message_feedback";

drop policy "Authenticated users can read metrics_hourly_aggregates" on "public"."metrics_hourly_aggregates";

drop policy "Service role has full access to metrics_hourly_aggregates" on "public"."metrics_hourly_aggregates";

drop policy "Allow authenticated read access to catalog health history" on "public"."model_catalog_health_history";

drop policy "Allow service role full access to catalog health history" on "public"."model_catalog_health_history";

drop policy "Authenticated users can read aggregates" on "public"."model_health_aggregates";

drop policy "Service role can do anything on aggregates" on "public"."model_health_aggregates";

drop policy "Authenticated users can read health history" on "public"."model_health_history";

drop policy "Service role can do anything on health history" on "public"."model_health_history";

drop policy "Authenticated users can read incidents" on "public"."model_health_incidents";

drop policy "Service role can do anything on incidents" on "public"."model_health_incidents";

drop policy "Authenticated users can read model health" on "public"."model_health_tracking";

drop policy "Service role can do anything on model health" on "public"."model_health_tracking";

drop policy "Allow authenticated read access to models" on "public"."models";

drop policy "Allow public read access to active models" on "public"."models";

drop policy "Allow service role full access to models" on "public"."models";

drop policy "Service role can manage all notification preferences" on "public"."notification_preferences";

drop policy "Users can insert their own notification preferences" on "public"."notification_preferences";

drop policy "Users can update their own notification preferences" on "public"."notification_preferences";

drop policy "Users can view their own notification preferences" on "public"."notification_preferences";

drop policy "Service role can manage all notifications" on "public"."notifications";

drop policy "Users can insert their own notifications" on "public"."notifications";

drop policy "Users can view their own notifications" on "public"."notifications";

drop policy "Service role has full access to payments" on "public"."payments";

drop policy "Users can view their own payments" on "public"."payments";

drop policy "Allow authenticated read access to providers" on "public"."providers";

drop policy "Allow public read access to providers" on "public"."providers";

drop policy "Allow service role full access to providers" on "public"."providers";

drop policy "Service role can manage rate limit usage" on "public"."rate_limit_usage";

drop policy "Users can read their own rate limit usage" on "public"."rate_limit_usage";

drop policy "Service role has full access" on "public"."referrals";

drop policy "Service role has full access to users" on "public"."users";

drop policy "Service role has full access" on "public"."users";

drop policy "Users can view their own data" on "public"."users";

revoke delete on table "public"."activity_log" from "anon";

revoke insert on table "public"."activity_log" from "anon";

revoke references on table "public"."activity_log" from "anon";

revoke select on table "public"."activity_log" from "anon";

revoke trigger on table "public"."activity_log" from "anon";

revoke truncate on table "public"."activity_log" from "anon";

revoke update on table "public"."activity_log" from "anon";

revoke delete on table "public"."activity_log" from "authenticated";

revoke insert on table "public"."activity_log" from "authenticated";

revoke references on table "public"."activity_log" from "authenticated";

revoke select on table "public"."activity_log" from "authenticated";

revoke trigger on table "public"."activity_log" from "authenticated";

revoke truncate on table "public"."activity_log" from "authenticated";

revoke update on table "public"."activity_log" from "authenticated";

revoke delete on table "public"."activity_log" from "service_role";

revoke insert on table "public"."activity_log" from "service_role";

revoke references on table "public"."activity_log" from "service_role";

revoke select on table "public"."activity_log" from "service_role";

revoke trigger on table "public"."activity_log" from "service_role";

revoke truncate on table "public"."activity_log" from "service_role";

revoke update on table "public"."activity_log" from "service_role";

revoke delete on table "public"."admin_users" from "anon";

revoke insert on table "public"."admin_users" from "anon";

revoke references on table "public"."admin_users" from "anon";

revoke select on table "public"."admin_users" from "anon";

revoke trigger on table "public"."admin_users" from "anon";

revoke truncate on table "public"."admin_users" from "anon";

revoke update on table "public"."admin_users" from "anon";

revoke delete on table "public"."admin_users" from "authenticated";

revoke insert on table "public"."admin_users" from "authenticated";

revoke references on table "public"."admin_users" from "authenticated";

revoke select on table "public"."admin_users" from "authenticated";

revoke trigger on table "public"."admin_users" from "authenticated";

revoke truncate on table "public"."admin_users" from "authenticated";

revoke update on table "public"."admin_users" from "authenticated";

revoke delete on table "public"."admin_users" from "service_role";

revoke insert on table "public"."admin_users" from "service_role";

revoke references on table "public"."admin_users" from "service_role";

revoke select on table "public"."admin_users" from "service_role";

revoke trigger on table "public"."admin_users" from "service_role";

revoke truncate on table "public"."admin_users" from "service_role";

revoke update on table "public"."admin_users" from "service_role";

revoke delete on table "public"."api_keys_new" from "anon";

revoke insert on table "public"."api_keys_new" from "anon";

revoke references on table "public"."api_keys_new" from "anon";

revoke select on table "public"."api_keys_new" from "anon";

revoke trigger on table "public"."api_keys_new" from "anon";

revoke truncate on table "public"."api_keys_new" from "anon";

revoke update on table "public"."api_keys_new" from "anon";

revoke delete on table "public"."api_keys_new" from "authenticated";

revoke insert on table "public"."api_keys_new" from "authenticated";

revoke references on table "public"."api_keys_new" from "authenticated";

revoke select on table "public"."api_keys_new" from "authenticated";

revoke trigger on table "public"."api_keys_new" from "authenticated";

revoke truncate on table "public"."api_keys_new" from "authenticated";

revoke update on table "public"."api_keys_new" from "authenticated";

revoke delete on table "public"."api_keys_new" from "service_role";

revoke insert on table "public"."api_keys_new" from "service_role";

revoke references on table "public"."api_keys_new" from "service_role";

revoke select on table "public"."api_keys_new" from "service_role";

revoke trigger on table "public"."api_keys_new" from "service_role";

revoke truncate on table "public"."api_keys_new" from "service_role";

revoke update on table "public"."api_keys_new" from "service_role";

revoke delete on table "public"."chat_messages" from "anon";

revoke insert on table "public"."chat_messages" from "anon";

revoke references on table "public"."chat_messages" from "anon";

revoke select on table "public"."chat_messages" from "anon";

revoke trigger on table "public"."chat_messages" from "anon";

revoke truncate on table "public"."chat_messages" from "anon";

revoke update on table "public"."chat_messages" from "anon";

revoke delete on table "public"."chat_messages" from "authenticated";

revoke insert on table "public"."chat_messages" from "authenticated";

revoke references on table "public"."chat_messages" from "authenticated";

revoke select on table "public"."chat_messages" from "authenticated";

revoke trigger on table "public"."chat_messages" from "authenticated";

revoke truncate on table "public"."chat_messages" from "authenticated";

revoke update on table "public"."chat_messages" from "authenticated";

revoke delete on table "public"."chat_messages" from "service_role";

revoke insert on table "public"."chat_messages" from "service_role";

revoke references on table "public"."chat_messages" from "service_role";

revoke select on table "public"."chat_messages" from "service_role";

revoke trigger on table "public"."chat_messages" from "service_role";

revoke truncate on table "public"."chat_messages" from "service_role";

revoke update on table "public"."chat_messages" from "service_role";

revoke delete on table "public"."chat_sessions" from "anon";

revoke insert on table "public"."chat_sessions" from "anon";

revoke references on table "public"."chat_sessions" from "anon";

revoke select on table "public"."chat_sessions" from "anon";

revoke trigger on table "public"."chat_sessions" from "anon";

revoke truncate on table "public"."chat_sessions" from "anon";

revoke update on table "public"."chat_sessions" from "anon";

revoke delete on table "public"."chat_sessions" from "authenticated";

revoke insert on table "public"."chat_sessions" from "authenticated";

revoke references on table "public"."chat_sessions" from "authenticated";

revoke select on table "public"."chat_sessions" from "authenticated";

revoke trigger on table "public"."chat_sessions" from "authenticated";

revoke truncate on table "public"."chat_sessions" from "authenticated";

revoke update on table "public"."chat_sessions" from "authenticated";

revoke delete on table "public"."chat_sessions" from "service_role";

revoke insert on table "public"."chat_sessions" from "service_role";

revoke references on table "public"."chat_sessions" from "service_role";

revoke select on table "public"."chat_sessions" from "service_role";

revoke trigger on table "public"."chat_sessions" from "service_role";

revoke truncate on table "public"."chat_sessions" from "service_role";

revoke update on table "public"."chat_sessions" from "service_role";

revoke delete on table "public"."coupon_redemptions" from "anon";

revoke insert on table "public"."coupon_redemptions" from "anon";

revoke references on table "public"."coupon_redemptions" from "anon";

revoke select on table "public"."coupon_redemptions" from "anon";

revoke trigger on table "public"."coupon_redemptions" from "anon";

revoke truncate on table "public"."coupon_redemptions" from "anon";

revoke update on table "public"."coupon_redemptions" from "anon";

revoke delete on table "public"."coupon_redemptions" from "authenticated";

revoke insert on table "public"."coupon_redemptions" from "authenticated";

revoke references on table "public"."coupon_redemptions" from "authenticated";

revoke select on table "public"."coupon_redemptions" from "authenticated";

revoke trigger on table "public"."coupon_redemptions" from "authenticated";

revoke truncate on table "public"."coupon_redemptions" from "authenticated";

revoke update on table "public"."coupon_redemptions" from "authenticated";

revoke delete on table "public"."coupon_redemptions" from "service_role";

revoke insert on table "public"."coupon_redemptions" from "service_role";

revoke references on table "public"."coupon_redemptions" from "service_role";

revoke select on table "public"."coupon_redemptions" from "service_role";

revoke trigger on table "public"."coupon_redemptions" from "service_role";

revoke truncate on table "public"."coupon_redemptions" from "service_role";

revoke update on table "public"."coupon_redemptions" from "service_role";

revoke delete on table "public"."coupons" from "anon";

revoke insert on table "public"."coupons" from "anon";

revoke references on table "public"."coupons" from "anon";

revoke select on table "public"."coupons" from "anon";

revoke trigger on table "public"."coupons" from "anon";

revoke truncate on table "public"."coupons" from "anon";

revoke update on table "public"."coupons" from "anon";

revoke delete on table "public"."coupons" from "authenticated";

revoke insert on table "public"."coupons" from "authenticated";

revoke references on table "public"."coupons" from "authenticated";

revoke select on table "public"."coupons" from "authenticated";

revoke trigger on table "public"."coupons" from "authenticated";

revoke truncate on table "public"."coupons" from "authenticated";

revoke update on table "public"."coupons" from "authenticated";

revoke delete on table "public"."coupons" from "service_role";

revoke insert on table "public"."coupons" from "service_role";

revoke references on table "public"."coupons" from "service_role";

revoke select on table "public"."coupons" from "service_role";

revoke trigger on table "public"."coupons" from "service_role";

revoke truncate on table "public"."coupons" from "service_role";

revoke update on table "public"."coupons" from "service_role";

revoke delete on table "public"."credit_transactions" from "anon";

revoke insert on table "public"."credit_transactions" from "anon";

revoke references on table "public"."credit_transactions" from "anon";

revoke select on table "public"."credit_transactions" from "anon";

revoke trigger on table "public"."credit_transactions" from "anon";

revoke truncate on table "public"."credit_transactions" from "anon";

revoke update on table "public"."credit_transactions" from "anon";

revoke delete on table "public"."credit_transactions" from "authenticated";

revoke insert on table "public"."credit_transactions" from "authenticated";

revoke references on table "public"."credit_transactions" from "authenticated";

revoke select on table "public"."credit_transactions" from "authenticated";

revoke trigger on table "public"."credit_transactions" from "authenticated";

revoke truncate on table "public"."credit_transactions" from "authenticated";

revoke update on table "public"."credit_transactions" from "authenticated";

revoke delete on table "public"."credit_transactions" from "service_role";

revoke insert on table "public"."credit_transactions" from "service_role";

revoke references on table "public"."credit_transactions" from "service_role";

revoke select on table "public"."credit_transactions" from "service_role";

revoke trigger on table "public"."credit_transactions" from "service_role";

revoke truncate on table "public"."credit_transactions" from "service_role";

revoke update on table "public"."credit_transactions" from "service_role";

revoke delete on table "public"."message_feedback" from "anon";

revoke insert on table "public"."message_feedback" from "anon";

revoke references on table "public"."message_feedback" from "anon";

revoke select on table "public"."message_feedback" from "anon";

revoke trigger on table "public"."message_feedback" from "anon";

revoke truncate on table "public"."message_feedback" from "anon";

revoke update on table "public"."message_feedback" from "anon";

revoke delete on table "public"."message_feedback" from "authenticated";

revoke insert on table "public"."message_feedback" from "authenticated";

revoke references on table "public"."message_feedback" from "authenticated";

revoke select on table "public"."message_feedback" from "authenticated";

revoke trigger on table "public"."message_feedback" from "authenticated";

revoke truncate on table "public"."message_feedback" from "authenticated";

revoke update on table "public"."message_feedback" from "authenticated";

revoke delete on table "public"."message_feedback" from "service_role";

revoke insert on table "public"."message_feedback" from "service_role";

revoke references on table "public"."message_feedback" from "service_role";

revoke select on table "public"."message_feedback" from "service_role";

revoke trigger on table "public"."message_feedback" from "service_role";

revoke truncate on table "public"."message_feedback" from "service_role";

revoke update on table "public"."message_feedback" from "service_role";

revoke delete on table "public"."metrics_hourly_aggregates" from "anon";

revoke insert on table "public"."metrics_hourly_aggregates" from "anon";

revoke references on table "public"."metrics_hourly_aggregates" from "anon";

revoke select on table "public"."metrics_hourly_aggregates" from "anon";

revoke trigger on table "public"."metrics_hourly_aggregates" from "anon";

revoke truncate on table "public"."metrics_hourly_aggregates" from "anon";

revoke update on table "public"."metrics_hourly_aggregates" from "anon";

revoke delete on table "public"."metrics_hourly_aggregates" from "authenticated";

revoke insert on table "public"."metrics_hourly_aggregates" from "authenticated";

revoke references on table "public"."metrics_hourly_aggregates" from "authenticated";

revoke select on table "public"."metrics_hourly_aggregates" from "authenticated";

revoke trigger on table "public"."metrics_hourly_aggregates" from "authenticated";

revoke truncate on table "public"."metrics_hourly_aggregates" from "authenticated";

revoke update on table "public"."metrics_hourly_aggregates" from "authenticated";

revoke delete on table "public"."metrics_hourly_aggregates" from "service_role";

revoke insert on table "public"."metrics_hourly_aggregates" from "service_role";

revoke references on table "public"."metrics_hourly_aggregates" from "service_role";

revoke select on table "public"."metrics_hourly_aggregates" from "service_role";

revoke trigger on table "public"."metrics_hourly_aggregates" from "service_role";

revoke truncate on table "public"."metrics_hourly_aggregates" from "service_role";

revoke update on table "public"."metrics_hourly_aggregates" from "service_role";

revoke delete on table "public"."model_catalog_health_history" from "anon";

revoke insert on table "public"."model_catalog_health_history" from "anon";

revoke references on table "public"."model_catalog_health_history" from "anon";

revoke select on table "public"."model_catalog_health_history" from "anon";

revoke trigger on table "public"."model_catalog_health_history" from "anon";

revoke truncate on table "public"."model_catalog_health_history" from "anon";

revoke update on table "public"."model_catalog_health_history" from "anon";

revoke delete on table "public"."model_catalog_health_history" from "authenticated";

revoke insert on table "public"."model_catalog_health_history" from "authenticated";

revoke references on table "public"."model_catalog_health_history" from "authenticated";

revoke select on table "public"."model_catalog_health_history" from "authenticated";

revoke trigger on table "public"."model_catalog_health_history" from "authenticated";

revoke truncate on table "public"."model_catalog_health_history" from "authenticated";

revoke update on table "public"."model_catalog_health_history" from "authenticated";

revoke delete on table "public"."model_catalog_health_history" from "service_role";

revoke insert on table "public"."model_catalog_health_history" from "service_role";

revoke references on table "public"."model_catalog_health_history" from "service_role";

revoke select on table "public"."model_catalog_health_history" from "service_role";

revoke trigger on table "public"."model_catalog_health_history" from "service_role";

revoke truncate on table "public"."model_catalog_health_history" from "service_role";

revoke update on table "public"."model_catalog_health_history" from "service_role";

revoke delete on table "public"."model_health_aggregates" from "anon";

revoke insert on table "public"."model_health_aggregates" from "anon";

revoke references on table "public"."model_health_aggregates" from "anon";

revoke select on table "public"."model_health_aggregates" from "anon";

revoke trigger on table "public"."model_health_aggregates" from "anon";

revoke truncate on table "public"."model_health_aggregates" from "anon";

revoke update on table "public"."model_health_aggregates" from "anon";

revoke delete on table "public"."model_health_aggregates" from "authenticated";

revoke insert on table "public"."model_health_aggregates" from "authenticated";

revoke references on table "public"."model_health_aggregates" from "authenticated";

revoke select on table "public"."model_health_aggregates" from "authenticated";

revoke trigger on table "public"."model_health_aggregates" from "authenticated";

revoke truncate on table "public"."model_health_aggregates" from "authenticated";

revoke update on table "public"."model_health_aggregates" from "authenticated";

revoke delete on table "public"."model_health_aggregates" from "service_role";

revoke insert on table "public"."model_health_aggregates" from "service_role";

revoke references on table "public"."model_health_aggregates" from "service_role";

revoke select on table "public"."model_health_aggregates" from "service_role";

revoke trigger on table "public"."model_health_aggregates" from "service_role";

revoke truncate on table "public"."model_health_aggregates" from "service_role";

revoke update on table "public"."model_health_aggregates" from "service_role";

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

revoke delete on table "public"."model_health_incidents" from "anon";

revoke insert on table "public"."model_health_incidents" from "anon";

revoke references on table "public"."model_health_incidents" from "anon";

revoke select on table "public"."model_health_incidents" from "anon";

revoke trigger on table "public"."model_health_incidents" from "anon";

revoke truncate on table "public"."model_health_incidents" from "anon";

revoke update on table "public"."model_health_incidents" from "anon";

revoke delete on table "public"."model_health_incidents" from "authenticated";

revoke insert on table "public"."model_health_incidents" from "authenticated";

revoke references on table "public"."model_health_incidents" from "authenticated";

revoke select on table "public"."model_health_incidents" from "authenticated";

revoke trigger on table "public"."model_health_incidents" from "authenticated";

revoke truncate on table "public"."model_health_incidents" from "authenticated";

revoke update on table "public"."model_health_incidents" from "authenticated";

revoke delete on table "public"."model_health_incidents" from "service_role";

revoke insert on table "public"."model_health_incidents" from "service_role";

revoke references on table "public"."model_health_incidents" from "service_role";

revoke select on table "public"."model_health_incidents" from "service_role";

revoke trigger on table "public"."model_health_incidents" from "service_role";

revoke truncate on table "public"."model_health_incidents" from "service_role";

revoke update on table "public"."model_health_incidents" from "service_role";

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

revoke delete on table "public"."notification_preferences" from "anon";

revoke insert on table "public"."notification_preferences" from "anon";

revoke references on table "public"."notification_preferences" from "anon";

revoke select on table "public"."notification_preferences" from "anon";

revoke trigger on table "public"."notification_preferences" from "anon";

revoke truncate on table "public"."notification_preferences" from "anon";

revoke update on table "public"."notification_preferences" from "anon";

revoke delete on table "public"."notification_preferences" from "authenticated";

revoke insert on table "public"."notification_preferences" from "authenticated";

revoke references on table "public"."notification_preferences" from "authenticated";

revoke select on table "public"."notification_preferences" from "authenticated";

revoke trigger on table "public"."notification_preferences" from "authenticated";

revoke truncate on table "public"."notification_preferences" from "authenticated";

revoke update on table "public"."notification_preferences" from "authenticated";

revoke delete on table "public"."notification_preferences" from "service_role";

revoke insert on table "public"."notification_preferences" from "service_role";

revoke references on table "public"."notification_preferences" from "service_role";

revoke select on table "public"."notification_preferences" from "service_role";

revoke trigger on table "public"."notification_preferences" from "service_role";

revoke truncate on table "public"."notification_preferences" from "service_role";

revoke update on table "public"."notification_preferences" from "service_role";

revoke delete on table "public"."notifications" from "anon";

revoke insert on table "public"."notifications" from "anon";

revoke references on table "public"."notifications" from "anon";

revoke select on table "public"."notifications" from "anon";

revoke trigger on table "public"."notifications" from "anon";

revoke truncate on table "public"."notifications" from "anon";

revoke update on table "public"."notifications" from "anon";

revoke delete on table "public"."notifications" from "authenticated";

revoke insert on table "public"."notifications" from "authenticated";

revoke references on table "public"."notifications" from "authenticated";

revoke select on table "public"."notifications" from "authenticated";

revoke trigger on table "public"."notifications" from "authenticated";

revoke truncate on table "public"."notifications" from "authenticated";

revoke update on table "public"."notifications" from "authenticated";

revoke delete on table "public"."notifications" from "service_role";

revoke insert on table "public"."notifications" from "service_role";

revoke references on table "public"."notifications" from "service_role";

revoke select on table "public"."notifications" from "service_role";

revoke trigger on table "public"."notifications" from "service_role";

revoke truncate on table "public"."notifications" from "service_role";

revoke update on table "public"."notifications" from "service_role";

revoke delete on table "public"."openrouter_apps" from "anon";

revoke insert on table "public"."openrouter_apps" from "anon";

revoke references on table "public"."openrouter_apps" from "anon";

revoke select on table "public"."openrouter_apps" from "anon";

revoke trigger on table "public"."openrouter_apps" from "anon";

revoke truncate on table "public"."openrouter_apps" from "anon";

revoke update on table "public"."openrouter_apps" from "anon";

revoke delete on table "public"."openrouter_apps" from "authenticated";

revoke insert on table "public"."openrouter_apps" from "authenticated";

revoke references on table "public"."openrouter_apps" from "authenticated";

revoke select on table "public"."openrouter_apps" from "authenticated";

revoke trigger on table "public"."openrouter_apps" from "authenticated";

revoke truncate on table "public"."openrouter_apps" from "authenticated";

revoke update on table "public"."openrouter_apps" from "authenticated";

revoke delete on table "public"."openrouter_apps" from "service_role";

revoke insert on table "public"."openrouter_apps" from "service_role";

revoke references on table "public"."openrouter_apps" from "service_role";

revoke select on table "public"."openrouter_apps" from "service_role";

revoke trigger on table "public"."openrouter_apps" from "service_role";

revoke truncate on table "public"."openrouter_apps" from "service_role";

revoke update on table "public"."openrouter_apps" from "service_role";

revoke delete on table "public"."openrouter_models" from "anon";

revoke insert on table "public"."openrouter_models" from "anon";

revoke references on table "public"."openrouter_models" from "anon";

revoke select on table "public"."openrouter_models" from "anon";

revoke trigger on table "public"."openrouter_models" from "anon";

revoke truncate on table "public"."openrouter_models" from "anon";

revoke update on table "public"."openrouter_models" from "anon";

revoke delete on table "public"."openrouter_models" from "authenticated";

revoke insert on table "public"."openrouter_models" from "authenticated";

revoke references on table "public"."openrouter_models" from "authenticated";

revoke select on table "public"."openrouter_models" from "authenticated";

revoke trigger on table "public"."openrouter_models" from "authenticated";

revoke truncate on table "public"."openrouter_models" from "authenticated";

revoke update on table "public"."openrouter_models" from "authenticated";

revoke delete on table "public"."openrouter_models" from "service_role";

revoke insert on table "public"."openrouter_models" from "service_role";

revoke references on table "public"."openrouter_models" from "service_role";

revoke select on table "public"."openrouter_models" from "service_role";

revoke trigger on table "public"."openrouter_models" from "service_role";

revoke truncate on table "public"."openrouter_models" from "service_role";

revoke update on table "public"."openrouter_models" from "service_role";

revoke delete on table "public"."payments" from "anon";

revoke insert on table "public"."payments" from "anon";

revoke references on table "public"."payments" from "anon";

revoke select on table "public"."payments" from "anon";

revoke trigger on table "public"."payments" from "anon";

revoke truncate on table "public"."payments" from "anon";

revoke update on table "public"."payments" from "anon";

revoke delete on table "public"."payments" from "authenticated";

revoke insert on table "public"."payments" from "authenticated";

revoke references on table "public"."payments" from "authenticated";

revoke select on table "public"."payments" from "authenticated";

revoke trigger on table "public"."payments" from "authenticated";

revoke truncate on table "public"."payments" from "authenticated";

revoke update on table "public"."payments" from "authenticated";

revoke delete on table "public"."payments" from "service_role";

revoke insert on table "public"."payments" from "service_role";

revoke references on table "public"."payments" from "service_role";

revoke select on table "public"."payments" from "service_role";

revoke trigger on table "public"."payments" from "service_role";

revoke truncate on table "public"."payments" from "service_role";

revoke update on table "public"."payments" from "service_role";

revoke delete on table "public"."plans" from "anon";

revoke insert on table "public"."plans" from "anon";

revoke references on table "public"."plans" from "anon";

revoke select on table "public"."plans" from "anon";

revoke trigger on table "public"."plans" from "anon";

revoke truncate on table "public"."plans" from "anon";

revoke update on table "public"."plans" from "anon";

revoke delete on table "public"."plans" from "authenticated";

revoke insert on table "public"."plans" from "authenticated";

revoke references on table "public"."plans" from "authenticated";

revoke select on table "public"."plans" from "authenticated";

revoke trigger on table "public"."plans" from "authenticated";

revoke truncate on table "public"."plans" from "authenticated";

revoke update on table "public"."plans" from "authenticated";

revoke delete on table "public"."plans" from "service_role";

revoke insert on table "public"."plans" from "service_role";

revoke references on table "public"."plans" from "service_role";

revoke select on table "public"."plans" from "service_role";

revoke trigger on table "public"."plans" from "service_role";

revoke truncate on table "public"."plans" from "service_role";

revoke update on table "public"."plans" from "service_role";

revoke delete on table "public"."pricing_tiers" from "anon";

revoke insert on table "public"."pricing_tiers" from "anon";

revoke references on table "public"."pricing_tiers" from "anon";

revoke select on table "public"."pricing_tiers" from "anon";

revoke trigger on table "public"."pricing_tiers" from "anon";

revoke truncate on table "public"."pricing_tiers" from "anon";

revoke update on table "public"."pricing_tiers" from "anon";

revoke delete on table "public"."pricing_tiers" from "authenticated";

revoke insert on table "public"."pricing_tiers" from "authenticated";

revoke references on table "public"."pricing_tiers" from "authenticated";

revoke select on table "public"."pricing_tiers" from "authenticated";

revoke trigger on table "public"."pricing_tiers" from "authenticated";

revoke truncate on table "public"."pricing_tiers" from "authenticated";

revoke update on table "public"."pricing_tiers" from "authenticated";

revoke delete on table "public"."pricing_tiers" from "service_role";

revoke insert on table "public"."pricing_tiers" from "service_role";

revoke references on table "public"."pricing_tiers" from "service_role";

revoke select on table "public"."pricing_tiers" from "service_role";

revoke trigger on table "public"."pricing_tiers" from "service_role";

revoke truncate on table "public"."pricing_tiers" from "service_role";

revoke update on table "public"."pricing_tiers" from "service_role";

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

revoke delete on table "public"."referrals" from "anon";

revoke insert on table "public"."referrals" from "anon";

revoke references on table "public"."referrals" from "anon";

revoke select on table "public"."referrals" from "anon";

revoke trigger on table "public"."referrals" from "anon";

revoke truncate on table "public"."referrals" from "anon";

revoke update on table "public"."referrals" from "anon";

revoke delete on table "public"."referrals" from "authenticated";

revoke insert on table "public"."referrals" from "authenticated";

revoke references on table "public"."referrals" from "authenticated";

revoke select on table "public"."referrals" from "authenticated";

revoke trigger on table "public"."referrals" from "authenticated";

revoke truncate on table "public"."referrals" from "authenticated";

revoke update on table "public"."referrals" from "authenticated";

revoke delete on table "public"."referrals" from "service_role";

revoke insert on table "public"."referrals" from "service_role";

revoke references on table "public"."referrals" from "service_role";

revoke select on table "public"."referrals" from "service_role";

revoke trigger on table "public"."referrals" from "service_role";

revoke truncate on table "public"."referrals" from "service_role";

revoke update on table "public"."referrals" from "service_role";

revoke delete on table "public"."role_permissions" from "anon";

revoke insert on table "public"."role_permissions" from "anon";

revoke references on table "public"."role_permissions" from "anon";

revoke select on table "public"."role_permissions" from "anon";

revoke trigger on table "public"."role_permissions" from "anon";

revoke truncate on table "public"."role_permissions" from "anon";

revoke update on table "public"."role_permissions" from "anon";

revoke delete on table "public"."role_permissions" from "authenticated";

revoke insert on table "public"."role_permissions" from "authenticated";

revoke references on table "public"."role_permissions" from "authenticated";

revoke select on table "public"."role_permissions" from "authenticated";

revoke trigger on table "public"."role_permissions" from "authenticated";

revoke truncate on table "public"."role_permissions" from "authenticated";

revoke update on table "public"."role_permissions" from "authenticated";

revoke delete on table "public"."role_permissions" from "service_role";

revoke insert on table "public"."role_permissions" from "service_role";

revoke references on table "public"."role_permissions" from "service_role";

revoke select on table "public"."role_permissions" from "service_role";

revoke trigger on table "public"."role_permissions" from "service_role";

revoke truncate on table "public"."role_permissions" from "service_role";

revoke update on table "public"."role_permissions" from "service_role";

revoke delete on table "public"."trial_config" from "anon";

revoke insert on table "public"."trial_config" from "anon";

revoke references on table "public"."trial_config" from "anon";

revoke select on table "public"."trial_config" from "anon";

revoke trigger on table "public"."trial_config" from "anon";

revoke truncate on table "public"."trial_config" from "anon";

revoke update on table "public"."trial_config" from "anon";

revoke delete on table "public"."trial_config" from "authenticated";

revoke insert on table "public"."trial_config" from "authenticated";

revoke references on table "public"."trial_config" from "authenticated";

revoke select on table "public"."trial_config" from "authenticated";

revoke trigger on table "public"."trial_config" from "authenticated";

revoke truncate on table "public"."trial_config" from "authenticated";

revoke update on table "public"."trial_config" from "authenticated";

revoke delete on table "public"."trial_config" from "service_role";

revoke insert on table "public"."trial_config" from "service_role";

revoke references on table "public"."trial_config" from "service_role";

revoke select on table "public"."trial_config" from "service_role";

revoke trigger on table "public"."trial_config" from "service_role";

revoke truncate on table "public"."trial_config" from "service_role";

revoke update on table "public"."trial_config" from "service_role";

revoke delete on table "public"."usage_records" from "anon";

revoke insert on table "public"."usage_records" from "anon";

revoke references on table "public"."usage_records" from "anon";

revoke select on table "public"."usage_records" from "anon";

revoke trigger on table "public"."usage_records" from "anon";

revoke truncate on table "public"."usage_records" from "anon";

revoke update on table "public"."usage_records" from "anon";

revoke delete on table "public"."usage_records" from "authenticated";

revoke insert on table "public"."usage_records" from "authenticated";

revoke references on table "public"."usage_records" from "authenticated";

revoke select on table "public"."usage_records" from "authenticated";

revoke trigger on table "public"."usage_records" from "authenticated";

revoke truncate on table "public"."usage_records" from "authenticated";

revoke update on table "public"."usage_records" from "authenticated";

revoke delete on table "public"."usage_records" from "service_role";

revoke insert on table "public"."usage_records" from "service_role";

revoke references on table "public"."usage_records" from "service_role";

revoke select on table "public"."usage_records" from "service_role";

revoke trigger on table "public"."usage_records" from "service_role";

revoke truncate on table "public"."usage_records" from "service_role";

revoke update on table "public"."usage_records" from "service_role";

revoke delete on table "public"."user_plans" from "anon";

revoke insert on table "public"."user_plans" from "anon";

revoke references on table "public"."user_plans" from "anon";

revoke select on table "public"."user_plans" from "anon";

revoke trigger on table "public"."user_plans" from "anon";

revoke truncate on table "public"."user_plans" from "anon";

revoke update on table "public"."user_plans" from "anon";

revoke delete on table "public"."user_plans" from "authenticated";

revoke insert on table "public"."user_plans" from "authenticated";

revoke references on table "public"."user_plans" from "authenticated";

revoke select on table "public"."user_plans" from "authenticated";

revoke trigger on table "public"."user_plans" from "authenticated";

revoke truncate on table "public"."user_plans" from "authenticated";

revoke update on table "public"."user_plans" from "authenticated";

revoke delete on table "public"."user_plans" from "service_role";

revoke insert on table "public"."user_plans" from "service_role";

revoke references on table "public"."user_plans" from "service_role";

revoke select on table "public"."user_plans" from "service_role";

revoke trigger on table "public"."user_plans" from "service_role";

revoke truncate on table "public"."user_plans" from "service_role";

revoke update on table "public"."user_plans" from "service_role";

revoke delete on table "public"."users" from "anon";

revoke insert on table "public"."users" from "anon";

revoke references on table "public"."users" from "anon";

revoke select on table "public"."users" from "anon";

revoke trigger on table "public"."users" from "anon";

revoke truncate on table "public"."users" from "anon";

revoke update on table "public"."users" from "anon";

revoke delete on table "public"."users" from "authenticated";

revoke insert on table "public"."users" from "authenticated";

revoke references on table "public"."users" from "authenticated";

revoke select on table "public"."users" from "authenticated";

revoke trigger on table "public"."users" from "authenticated";

revoke truncate on table "public"."users" from "authenticated";

revoke update on table "public"."users" from "authenticated";

revoke delete on table "public"."users" from "service_role";

revoke insert on table "public"."users" from "service_role";

revoke references on table "public"."users" from "service_role";

revoke select on table "public"."users" from "service_role";

revoke trigger on table "public"."users" from "service_role";

revoke truncate on table "public"."users" from "service_role";

revoke update on table "public"."users" from "service_role";

alter table "public"."activity_log" drop constraint "fk_user";

alter table "public"."admin_users" drop constraint "admin_users_created_by_fkey";

alter table "public"."admin_users" drop constraint "admin_users_email_key";

alter table "public"."admin_users" drop constraint "admin_users_role_check";

alter table "public"."admin_users" drop constraint "admin_users_status_check";

alter table "public"."api_keys_new" drop constraint "api_keys_new_api_key_key";

alter table "public"."api_keys_new" drop constraint "api_keys_new_key_hash_key";

alter table "public"."api_keys_new" drop constraint "api_keys_new_user_id_fkey";

alter table "public"."chat_messages" drop constraint "chat_messages_role_check";

alter table "public"."chat_messages" drop constraint "fk_chat_messages_session_id";

alter table "public"."chat_sessions" drop constraint "fk_chat_sessions_user_id";

alter table "public"."coupon_redemptions" drop constraint "balance_change_matches_value";

alter table "public"."coupon_redemptions" drop constraint "coupon_redemptions_coupon_id_fkey";

alter table "public"."coupon_redemptions" drop constraint "coupon_redemptions_user_id_fkey";

alter table "public"."coupon_redemptions" drop constraint "coupon_redemptions_value_applied_check";

alter table "public"."coupon_redemptions" drop constraint "uq_coupon_user";

alter table "public"."coupons" drop constraint "coupons_assigned_to_user_id_fkey";

alter table "public"."coupons" drop constraint "coupons_code_key";

alter table "public"."coupons" drop constraint "coupons_created_by_fkey";

alter table "public"."coupons" drop constraint "coupons_max_uses_check";

alter table "public"."coupons" drop constraint "coupons_times_used_check";

alter table "public"."coupons" drop constraint "coupons_value_usd_check";

alter table "public"."coupons" drop constraint "times_used_within_limit";

alter table "public"."coupons" drop constraint "user_specific_max_uses";

alter table "public"."coupons" drop constraint "user_specific_must_have_user";

alter table "public"."coupons" drop constraint "valid_date_range";

alter table "public"."credit_transactions" drop constraint "fk_payment";

alter table "public"."credit_transactions" drop constraint "fk_user";

alter table "public"."message_feedback" drop constraint "message_feedback_message_id_fkey";

alter table "public"."message_feedback" drop constraint "message_feedback_rating_check";

alter table "public"."message_feedback" drop constraint "message_feedback_session_id_fkey";

alter table "public"."message_feedback" drop constraint "message_feedback_user_id_fkey";

alter table "public"."metrics_hourly_aggregates" drop constraint "metrics_hourly_aggregates_hour_provider_model_key";

alter table "public"."model_catalog_health_history" drop constraint "model_catalog_health_history_health_status_check";

alter table "public"."model_catalog_health_history" drop constraint "model_catalog_health_history_model_id_fkey";

alter table "public"."model_health_incidents" drop constraint "model_health_incidents_provider_model_fkey";

alter table "public"."models" drop constraint "models_health_status_check";

alter table "public"."models" drop constraint "models_provider_id_fkey";

alter table "public"."models" drop constraint "unique_provider_model";

alter table "public"."notification_preferences" drop constraint "notification_preferences_user_id_fkey";

alter table "public"."notification_preferences" drop constraint "notification_preferences_user_id_key";

alter table "public"."notifications" drop constraint "notifications_user_id_fkey";

alter table "public"."openrouter_apps" drop constraint "unique_app_period";

alter table "public"."openrouter_models" drop constraint "unique_model_author_period";

alter table "public"."payments" drop constraint "payments_amount_cents_check";

alter table "public"."payments" drop constraint "payments_amount_usd_check";

alter table "public"."payments" drop constraint "payments_bonus_credits_check";

alter table "public"."payments" drop constraint "payments_credits_purchased_check";

alter table "public"."payments" drop constraint "payments_user_id_fkey";

alter table "public"."plans" drop constraint "plans_name_key";

alter table "public"."pricing_tiers" drop constraint "pricing_tiers_tier_name_key";

alter table "public"."providers" drop constraint "providers_health_status_check";

alter table "public"."providers" drop constraint "providers_name_key";

alter table "public"."providers" drop constraint "providers_slug_key";

alter table "public"."rate_limit_usage" drop constraint "rate_limit_usage_unique";

alter table "public"."rate_limit_usage" drop constraint "rate_limit_usage_user_id_fkey";

alter table "public"."referrals" drop constraint "referrals_referred_user_id_fkey";

alter table "public"."referrals" drop constraint "referrals_referrer_id_fkey";

alter table "public"."role_permissions" drop constraint "role_permissions_role_resource_action_key";

alter table "public"."usage_records" drop constraint "usage_records_user_id_fkey";

alter table "public"."user_plans" drop constraint "user_plans_plan_id_fkey";

alter table "public"."user_plans" drop constraint "user_plans_user_id_fkey";

alter table "public"."users" drop constraint "users_api_key_key";

alter table "public"."users" drop constraint "users_credits_non_negative";

alter table "public"."users" drop constraint "users_email_key";

alter table "public"."users" drop constraint "users_privy_user_id_key";

alter table "public"."users" drop constraint "users_username_key";

drop index if exists "public"."idx_provider_stats_24h_provider";

drop function if exists "public"."assign_chat_message_sequence"();

drop function if exists "public"."calculate_model_priority_score"(p_usage_count_24h integer, p_consecutive_failures integer, p_uptime_24h numeric, p_last_called_at timestamp with time zone, p_monitoring_tier text);

drop function if exists "public"."clean_old_health_history"(retention_days integer);

drop function if exists "public"."generate_referral_code"();

drop function if exists "public"."get_available_coupons"(p_user_id bigint);

drop function if exists "public"."is_coupon_redeemable"(p_coupon_code character varying, p_user_id bigint);

drop view if exists "public"."latest_apps";

drop view if exists "public"."latest_models";

drop view if exists "public"."model_status_current";

drop view if exists "public"."provider_health_current";

drop materialized view if exists "public"."provider_stats_24h";

drop function if exists "public"."refresh_provider_stats_24h"();

drop function if exists "public"."update_incident_duration"();

drop function if exists "public"."update_message_feedback_updated_at"();

drop function if exists "public"."update_metrics_hourly_updated_at"();

drop function if exists "public"."update_model_health_incidents_updated_at"();

drop function if exists "public"."update_model_health_tracking_updated_at"();

drop function if exists "public"."update_model_tier"();

drop function if exists "public"."update_notifications_updated_at"();

drop function if exists "public"."update_updated_at_column"();

alter table "public"."activity_log" drop constraint "activity_log_pkey";

alter table "public"."admin_users" drop constraint "admin_users_pkey";

alter table "public"."api_keys_new" drop constraint "api_keys_new_pkey";

alter table "public"."chat_messages" drop constraint "chat_messages_pkey";

alter table "public"."chat_sessions" drop constraint "chat_sessions_pkey";

alter table "public"."coupon_redemptions" drop constraint "coupon_redemptions_pkey";

alter table "public"."coupons" drop constraint "coupons_pkey";

alter table "public"."credit_transactions" drop constraint "credit_transactions_pkey";

alter table "public"."message_feedback" drop constraint "message_feedback_pkey";

alter table "public"."metrics_hourly_aggregates" drop constraint "metrics_hourly_aggregates_pkey";

alter table "public"."model_catalog_health_history" drop constraint "model_catalog_health_history_pkey";

alter table "public"."model_health_aggregates" drop constraint "model_health_aggregates_pkey";

alter table "public"."model_health_history" drop constraint "model_health_history_pkey";

alter table "public"."model_health_incidents" drop constraint "model_health_incidents_pkey";

alter table "public"."model_health_tracking" drop constraint "model_health_tracking_pkey";

alter table "public"."models" drop constraint "models_pkey";

alter table "public"."notification_preferences" drop constraint "notification_preferences_pkey";

alter table "public"."notifications" drop constraint "notifications_pkey";

alter table "public"."openrouter_apps" drop constraint "openrouter_apps_pkey";

alter table "public"."openrouter_models" drop constraint "openrouter_models_pkey";

alter table "public"."payments" drop constraint "payments_pkey";

alter table "public"."plans" drop constraint "plans_pkey";

alter table "public"."pricing_tiers" drop constraint "pricing_tiers_pkey";

alter table "public"."providers" drop constraint "providers_pkey";

alter table "public"."rate_limit_usage" drop constraint "rate_limit_usage_pkey";

alter table "public"."referrals" drop constraint "referrals_pkey";

alter table "public"."role_permissions" drop constraint "role_permissions_pkey";

alter table "public"."trial_config" drop constraint "trial_config_pkey";

alter table "public"."usage_records" drop constraint "usage_records_pkey";

alter table "public"."user_plans" drop constraint "user_plans_pkey";

alter table "public"."users" drop constraint "users_pkey";

drop index if exists "public"."activity_log_pkey";

drop index if exists "public"."admin_users_email_key";

drop index if exists "public"."admin_users_pkey";

drop index if exists "public"."api_keys_new_api_key_key";

drop index if exists "public"."api_keys_new_key_hash_key";

drop index if exists "public"."api_keys_new_pkey";

drop index if exists "public"."chat_messages_pkey";

drop index if exists "public"."chat_sessions_pkey";

drop index if exists "public"."coupon_redemptions_pkey";

drop index if exists "public"."coupons_code_key";

drop index if exists "public"."coupons_pkey";

drop index if exists "public"."credit_transactions_pkey";

drop index if exists "public"."idx_activity_log_cost";

drop index if exists "public"."idx_activity_log_created_at_desc";

drop index if exists "public"."idx_activity_log_gateway";

drop index if exists "public"."idx_activity_log_gateway_time";

drop index if exists "public"."idx_activity_log_model";

drop index if exists "public"."idx_activity_log_model_time";

drop index if exists "public"."idx_activity_log_provider";

drop index if exists "public"."idx_activity_log_provider_time";

drop index if exists "public"."idx_activity_log_timestamp";

drop index if exists "public"."idx_activity_log_timestamp_desc";

drop index if exists "public"."idx_activity_log_tokens";

drop index if exists "public"."idx_activity_log_user_id";

drop index if exists "public"."idx_activity_log_user_time";

drop index if exists "public"."idx_activity_log_user_timestamp";

drop index if exists "public"."idx_admin_users_created_at";

drop index if exists "public"."idx_admin_users_email";

drop index if exists "public"."idx_admin_users_role";

drop index if exists "public"."idx_admin_users_status";

drop index if exists "public"."idx_aggregates_gateway_period";

drop index if exists "public"."idx_aggregates_period";

drop index if exists "public"."idx_aggregates_provider_period";

drop index if exists "public"."idx_api_keys_api_key";

drop index if exists "public"."idx_api_keys_environment";

drop index if exists "public"."idx_api_keys_is_active";

drop index if exists "public"."idx_api_keys_key_hash";

drop index if exists "public"."idx_api_keys_new_is_trial";

drop index if exists "public"."idx_api_keys_new_key_hash";

drop index if exists "public"."idx_api_keys_new_subscription_status";

drop index if exists "public"."idx_api_keys_new_trial_dates";

drop index if exists "public"."idx_api_keys_user_id";

drop index if exists "public"."idx_chat_messages_created_at";

drop index if exists "public"."idx_chat_messages_duplicate_check";

drop index if exists "public"."idx_chat_messages_role";

drop index if exists "public"."idx_chat_messages_sequence";

drop index if exists "public"."idx_chat_messages_session_created";

drop index if exists "public"."idx_chat_messages_session_id";

drop index if exists "public"."idx_chat_sessions_is_active";

drop index if exists "public"."idx_chat_sessions_updated_at";

drop index if exists "public"."idx_chat_sessions_user_active";

drop index if exists "public"."idx_chat_sessions_user_id";

drop index if exists "public"."idx_coupons_active_global";

drop index if exists "public"."idx_coupons_assigned_user";

drop index if exists "public"."idx_coupons_code_upper";

drop index if exists "public"."idx_coupons_created_by";

drop index if exists "public"."idx_coupons_validity";

drop index if exists "public"."idx_credit_transactions_created_at";

drop index if exists "public"."idx_credit_transactions_type";

drop index if exists "public"."idx_credit_transactions_user_id";

drop index if exists "public"."idx_history_checked_at";

drop index if exists "public"."idx_history_gateway";

drop index if exists "public"."idx_history_gateway_time";

drop index if exists "public"."idx_history_provider_model_time";

drop index if exists "public"."idx_history_status";

drop index if exists "public"."idx_incidents_gateway";

drop index if exists "public"."idx_incidents_provider_model";

drop index if exists "public"."idx_incidents_severity";

drop index if exists "public"."idx_incidents_started_at";

drop index if exists "public"."idx_incidents_status";

drop index if exists "public"."idx_message_feedback_created_at";

drop index if exists "public"."idx_message_feedback_message_id";

drop index if exists "public"."idx_message_feedback_model";

drop index if exists "public"."idx_message_feedback_model_type";

drop index if exists "public"."idx_message_feedback_session_id";

drop index if exists "public"."idx_message_feedback_session_type";

drop index if exists "public"."idx_message_feedback_type";

drop index if exists "public"."idx_message_feedback_user_id";

drop index if exists "public"."idx_message_feedback_user_type";

drop index if exists "public"."idx_metrics_hourly_created_at";

drop index if exists "public"."idx_metrics_hourly_hour";

drop index if exists "public"."idx_metrics_hourly_model";

drop index if exists "public"."idx_metrics_hourly_provider";

drop index if exists "public"."idx_metrics_hourly_provider_model";

drop index if exists "public"."idx_model_catalog_health_history_checked_at";

drop index if exists "public"."idx_model_catalog_health_history_model_id";

drop index if exists "public"."idx_model_health_circuit_breaker";

drop index if exists "public"."idx_model_health_gateway";

drop index if exists "public"."idx_model_health_last_called";

drop index if exists "public"."idx_model_health_monitoring_tier";

drop index if exists "public"."idx_model_health_next_check";

drop index if exists "public"."idx_model_health_priority";

drop index if exists "public"."idx_model_health_provider";

drop index if exists "public"."idx_model_health_status";

drop index if exists "public"."idx_model_health_uptime";

drop index if exists "public"."idx_models_health_status";

drop index if exists "public"."idx_models_is_active";

drop index if exists "public"."idx_models_modality";

drop index if exists "public"."idx_models_model_id";

drop index if exists "public"."idx_models_provider_active";

drop index if exists "public"."idx_models_provider_id";

drop index if exists "public"."idx_models_provider_model_id";

drop index if exists "public"."idx_notification_preferences_user_id";

drop index if exists "public"."idx_notifications_created_at";

drop index if exists "public"."idx_notifications_status";

drop index if exists "public"."idx_notifications_type";

drop index if exists "public"."idx_notifications_user_id";

drop index if exists "public"."idx_notifications_user_type";

drop index if exists "public"."idx_openrouter_apps_rank";

drop index if exists "public"."idx_openrouter_apps_scraped_at";

drop index if exists "public"."idx_openrouter_apps_time_period";

drop index if exists "public"."idx_openrouter_models_rank";

drop index if exists "public"."idx_openrouter_models_scraped_at";

drop index if exists "public"."idx_openrouter_models_time_period";

drop index if exists "public"."idx_payments_created_at";

drop index if exists "public"."idx_payments_status";

drop index if exists "public"."idx_payments_stripe_intent";

drop index if exists "public"."idx_payments_stripe_session";

drop index if exists "public"."idx_payments_user_id";

drop index if exists "public"."idx_payments_user_status";

drop index if exists "public"."idx_providers_health_status";

drop index if exists "public"."idx_providers_is_active";

drop index if exists "public"."idx_providers_slug";

drop index if exists "public"."idx_redemptions_coupon";

drop index if exists "public"."idx_redemptions_ip";

drop index if exists "public"."idx_redemptions_timestamp";

drop index if exists "public"."idx_redemptions_user";

drop index if exists "public"."idx_referrals_code";

drop index if exists "public"."idx_referrals_referred_user_id";

drop index if exists "public"."idx_referrals_referrer_id";

drop index if exists "public"."idx_usage_records_api_key";

drop index if exists "public"."idx_usage_records_cost";

drop index if exists "public"."idx_usage_records_created_at";

drop index if exists "public"."idx_usage_records_model";

drop index if exists "public"."idx_usage_records_timestamp";

drop index if exists "public"."idx_usage_records_user_id";

drop index if exists "public"."idx_usage_records_user_time";

drop index if exists "public"."idx_user_plans_plan_id";

drop index if exists "public"."idx_user_plans_user_id";

drop index if exists "public"."idx_users_api_key";

drop index if exists "public"."idx_users_credits";

drop index if exists "public"."idx_users_email";

drop index if exists "public"."idx_users_privy_user_id";

drop index if exists "public"."idx_users_referral_code";

drop index if exists "public"."idx_users_referred_by_code";

drop index if exists "public"."idx_users_role";

drop index if exists "public"."idx_users_stripe_customer_id";

drop index if exists "public"."idx_users_stripe_subscription_id";

drop index if exists "public"."idx_users_subscription_status";

drop index if exists "public"."idx_users_tier";

drop index if exists "public"."message_feedback_pkey";

drop index if exists "public"."metrics_hourly_aggregates_hour_provider_model_key";

drop index if exists "public"."metrics_hourly_aggregates_pkey";

drop index if exists "public"."model_catalog_health_history_pkey";

drop index if exists "public"."model_health_aggregates_pkey";

drop index if exists "public"."model_health_history_pkey";

drop index if exists "public"."model_health_incidents_pkey";

drop index if exists "public"."model_health_tracking_pkey";

drop index if exists "public"."models_pkey";

drop index if exists "public"."notification_preferences_pkey";

drop index if exists "public"."notification_preferences_user_id_key";

drop index if exists "public"."notifications_pkey";

drop index if exists "public"."openrouter_apps_pkey";

drop index if exists "public"."openrouter_models_pkey";

drop index if exists "public"."payments_pkey";

drop index if exists "public"."plans_name_key";

drop index if exists "public"."plans_pkey";

drop index if exists "public"."pricing_tiers_pkey";

drop index if exists "public"."pricing_tiers_tier_name_key";

drop index if exists "public"."providers_name_key";

drop index if exists "public"."providers_pkey";

drop index if exists "public"."providers_slug_key";

drop index if exists "public"."rate_limit_usage_api_key_idx";

drop index if exists "public"."rate_limit_usage_pkey";

drop index if exists "public"."rate_limit_usage_unique";

drop index if exists "public"."rate_limit_usage_user_id_idx";

drop index if exists "public"."rate_limit_usage_window_start_idx";

drop index if exists "public"."rate_limit_usage_window_type_idx";

drop index if exists "public"."referrals_pkey";

drop index if exists "public"."role_permissions_pkey";

drop index if exists "public"."role_permissions_role_resource_action_key";

drop index if exists "public"."trial_config_pkey";

drop index if exists "public"."unique_app_period";

drop index if exists "public"."unique_model_author_period";

drop index if exists "public"."unique_provider_model";

drop index if exists "public"."uq_coupon_user";

drop index if exists "public"."usage_records_pkey";

drop index if exists "public"."user_plans_pkey";

drop index if exists "public"."users_api_key_key";

drop index if exists "public"."users_email_key";

drop index if exists "public"."users_pkey";

drop index if exists "public"."users_privy_user_id_key";

drop index if exists "public"."users_username_key";

drop table "public"."activity_log";

drop table "public"."admin_users";

drop table "public"."api_keys_new";

drop table "public"."chat_messages";

drop table "public"."chat_sessions";

drop table "public"."coupon_redemptions";

drop table "public"."coupons";

drop table "public"."credit_transactions";

drop table "public"."message_feedback";

drop table "public"."metrics_hourly_aggregates";

drop table "public"."model_catalog_health_history";

drop table "public"."model_health_aggregates";

drop table "public"."model_health_history";

drop table "public"."model_health_incidents";

drop table "public"."model_health_tracking";

drop table "public"."models";

drop table "public"."notification_preferences";

drop table "public"."notifications";

drop table "public"."openrouter_apps";

drop table "public"."openrouter_models";

drop table "public"."payments";

drop table "public"."plans";

drop table "public"."pricing_tiers";

drop table "public"."providers";

drop table "public"."rate_limit_usage";

drop table "public"."referrals";

drop table "public"."role_permissions";

drop table "public"."trial_config";

drop table "public"."usage_records";

drop table "public"."user_plans";

drop table "public"."users";

drop sequence if exists "public"."api_keys_new_id_seq";

drop sequence if exists "public"."chat_messages_id_seq";

drop sequence if exists "public"."chat_sessions_id_seq";

drop sequence if exists "public"."coupon_redemptions_id_seq";

drop sequence if exists "public"."coupons_id_seq";

drop sequence if exists "public"."message_feedback_id_seq";

drop sequence if exists "public"."metrics_hourly_aggregates_id_seq";

drop sequence if exists "public"."model_catalog_health_history_id_seq";

drop sequence if exists "public"."model_health_history_id_seq";

drop sequence if exists "public"."model_health_incidents_id_seq";

drop sequence if exists "public"."models_id_seq";

drop sequence if exists "public"."notification_preferences_id_seq";

drop sequence if exists "public"."notifications_id_seq";

drop sequence if exists "public"."openrouter_apps_id_seq";

drop sequence if exists "public"."openrouter_models_id_seq";

drop sequence if exists "public"."payments_id_seq";

drop sequence if exists "public"."plans_id_seq";

drop sequence if exists "public"."pricing_tiers_id_seq";

drop sequence if exists "public"."providers_id_seq";

drop sequence if exists "public"."rate_limit_usage_id_seq";

drop sequence if exists "public"."referrals_id_seq";

drop sequence if exists "public"."role_permissions_id_seq";

drop sequence if exists "public"."trial_config_id_seq";

drop sequence if exists "public"."usage_records_id_seq";

drop sequence if exists "public"."user_plans_id_seq";

drop sequence if exists "public"."users_id_seq";

drop type "public"."coupon_scope_type";

drop type "public"."coupon_type_enum";

drop type "public"."creator_type_enum";

drop type "public"."feedback_type";

drop type "public"."notification_channel";

drop type "public"."notification_status";

drop type "public"."notification_type";


