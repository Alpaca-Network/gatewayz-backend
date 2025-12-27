# User Communication Templates

Email templates for communicating the unified chat endpoint migration to users.

---

## Email 1: Initial Announcement (Week 1)

**Subject:** Introducing Our New Unified Chat API Endpoint

**Body:**

Hi [Name],

We're excited to announce a major improvement to the Gatewayz API: the **Unified Chat Endpoint**!

### What's New?

We've consolidated all our chat endpoints into a single, intelligent endpoint:

**`POST https://api.gatewayz.ai/v1/chat`**

This new endpoint:
- ✅ Automatically detects your request format (OpenAI, Anthropic, Responses API)
- ✅ Supports all models and providers
- ✅ Works with all existing SDKs (OpenAI, Anthropic, Vercel AI)
- ✅ Maintains backward compatibility
- ✅ Provides better performance

### Do I Need to Change Anything?

**Not immediately!** Your existing endpoints will continue working until **June 1, 2025**. However, we recommend migrating when convenient.

### Migration is Simple

Just change your base URL:

**Before:**
```
POST https://api.gatewayz.ai/v1/chat/completions
```

**After:**
```
POST https://api.gatewayz.ai/v1/chat
```

Everything else stays exactly the same - same requests, same responses, same API key.

### Learn More

- **Migration Guide:** https://docs.gatewayz.ai/migration/unified-chat
- **API Documentation:** https://docs.gatewayz.ai/api/v1/chat
- **FAQs:** https://docs.gatewayz.ai/faq/unified-chat

### Need Help?

Our team is here to assist with your migration:
- Email: support@gatewayz.ai
- Discord: https://discord.gg/gatewayz
- Schedule a call: https://calendly.com/gatewayz/migration-support

Happy coding!

The Gatewayz Team

---

## Email 2: Migration Reminder (Month 4 - March 2025)

**Subject:** Reminder: Migrate to /v1/chat by June 1st

**Body:**

Hi [Name],

This is a friendly reminder about our Unified Chat API endpoint migration.

### Important Dates

- **Now - May 31, 2025:** Both old and new endpoints work
- **June 1, 2025:** Legacy endpoints will be sunset

### Quick Migration Checklist

- [ ] Update base URL to `/v1/chat`
- [ ] Test in your staging environment
- [ ] Deploy to production
- [ ] Remove references to old endpoints

### Your Current Usage

We see you're currently using:
- [X] `/v1/chat/completions` - [request_count] requests/month
- [ ] `/v1/messages`
- [ ] `/v1/responses`

Migrating is simple and takes just a few minutes. See our [migration guide](https://docs.gatewayz.ai/migration/unified-chat) for step-by-step instructions.

### Need Assistance?

We're offering free migration support:
- Book a 15-minute migration call: https://calendly.com/gatewayz/migration-support
- Email support: support@gatewayz.ai
- Live chat: Available in your dashboard

Thank you for being a valued Gatewayz user!

The Gatewayz Team

---

## Email 3: Final Warning (Month 5 - April 2025)

**Subject:** Action Required: Legacy Endpoints Sunset in 30 Days

**Body:**

Hi [Name],

The legacy chat endpoints will be sunset in **30 days** (June 1, 2025).

### What This Means

After May 31, 2025, these endpoints will stop working:
- ❌ `/v1/chat/completions`
- ❌ `/v1/messages`
- ❌ `/v1/responses`
- ❌ `/api/chat/ai-sdk`

**You must migrate to `/v1/chat` to continue service.**

### Migration Status

Based on our logs, it appears you haven't migrated yet. Here's what you need to do:

**1. Update Your Code (5 minutes)**
```python
# Before
url = "https://api.gatewayz.ai/v1/chat/completions"

# After
url = "https://api.gatewayz.ai/v1/chat"
```

**2. Test (10 minutes)**
Run a few test requests to verify everything works.

**3. Deploy (varies)**
Deploy your updated code to production.

### Get Help Now

Don't wait until the last minute! We're here to help:

- **Priority Support:** Email support@gatewayz.ai with subject "Urgent Migration"
- **Schedule Emergency Call:** https://calendly.com/gatewayz/emergency-migration
- **Live Chat:** Available 24/7 in your dashboard

### Resources

- Migration Guide: https://docs.gatewayz.ai/migration/unified-chat
- Code Examples: https://docs.gatewayz.ai/examples/unified-chat
- Video Tutorial: https://www.youtube.com/watch?v=...

Please migrate before May 31st to avoid service disruption.

The Gatewayz Team

---

## Email 4: Final Countdown (2 Weeks Before - Mid-May 2025)

**Subject:** 🚨 Urgent: 14 Days Until Legacy Endpoints Sunset

**Body:**

Hi [Name],

**This is your final warning.** Legacy endpoints will stop working in **14 days** (June 1, 2025).

### ⚠️ Action Required Immediately

Our system shows you're still using legacy endpoints:
- `/v1/chat/completions`: [request_count] requests in the last 7 days

**These requests will fail after May 31, 2025.**

### Migrate in 3 Steps (Takes 15 minutes)

**Step 1:** Update your base URL
```
https://api.gatewayz.ai/v1/chat
```

**Step 2:** Test with one request
```bash
curl https://api.gatewayz.ai/v1/chat \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "test"}]
  }'
```

**Step 3:** Deploy your changes

### Emergency Support Available

We've extended support hours for the migration:

- **24/7 Live Chat:** Available in your dashboard
- **Emergency Hotline:** +1-XXX-XXX-XXXX
- **Dedicated Slack Channel:** #migration-help
- **Screen Share Support:** https://meet.gatewayz.ai/migration

### What Happens if I Don't Migrate?

After May 31, 2025 at 11:59 PM UTC:
- ❌ Legacy endpoints will return 410 Gone
- ❌ Your applications will fail
- ❌ You'll need to emergency deploy fixes

**Don't risk downtime - migrate today!**

The Gatewayz Team

P.S. Reply to this email if you need any help. We respond within 1 hour.

---

## Email 5: Post-Sunset (June 2, 2025)

**Subject:** Legacy Endpoints Have Been Sunset

**Body:**

Hi [Name],

As communicated over the past 4 months, we've sunset the legacy chat endpoints as of June 1, 2025.

### What Changed

These endpoints now return `410 Gone`:
- `/v1/chat/completions`
- `/v1/messages`
- `/v1/responses`
- `/api/chat/ai-sdk`

### If You Haven't Migrated Yet

**You'll see errors in your application.** Here's how to fix it immediately:

**Quick Fix (5 minutes):**
1. Update all API calls to use `/v1/chat`
2. Deploy your changes
3. Verify requests are working

### Need Emergency Help?

- **Priority Support:** support@gatewayz.ai (response within 30 minutes)
- **Emergency Call:** https://meet.gatewayz.ai/emergency
- **Migration Guide:** https://docs.gatewayz.ai/migration/unified-chat

We're here to help you get back online quickly.

The Gatewayz Team

---

## Blog Post Template

**Title:** Introducing the Unified Chat API: One Endpoint for All Models

**Introduction:**

Today, we're excited to announce the Unified Chat API - a major improvement to how you interact with AI models through Gatewayz.

**What's New?**

We've consolidated five separate endpoints into one intelligent endpoint: `/v1/chat`

This new endpoint automatically detects your request format and handles:
- OpenAI chat completions
- Anthropic messages
- OpenAI Responses API
- Custom formats

**Why We Built This**

Over the past year, we've added support for 15+ AI providers and multiple API formats. While this gave you flexibility, it also created complexity:
- Which endpoint should I use?
- Do I need to change my code when switching providers?
- Why are there so many similar endpoints?

The Unified Chat API solves these problems.

**How It Works**

The endpoint uses intelligent format detection:

[Code examples...]

**Migration Timeline**

[Timeline details...]

**What's Next?**

All future features will be built on the unified endpoint:
- Enhanced streaming
- Multi-modal support
- Advanced tool calling
- Custom response formats

**Get Started**

[Links to docs, migration guide, etc.]

---

## Discord/Slack Announcement

```
🎉 **Exciting News: Unified Chat API is Live!**

We've consolidated all chat endpoints into one: `/v1/chat`

✨ Auto-detects request format (OpenAI, Anthropic, etc.)
✨ Works with all providers and models
✨ Simpler API, better performance

📚 Migration Guide: https://docs.gatewayz.ai/migration/unified-chat
💬 Questions? Ask in #api-help

Old endpoints work until June 1, 2025. Migrate when convenient!
```

---

## Tweet Template

```
🚀 Introducing the Unified Chat API!

One endpoint. All models. Auto-format detection.

POST /v1/chat

- OpenAI, Anthropic, all providers
- Same requests, same responses
- Better performance

Migration guide: [link]

#AI #API #DevTools
```

---

## FAQ Page Content

**Q: Why are you making this change?**
A: To simplify the API and improve your developer experience. One endpoint is easier to use, maintain, and optimize than five separate ones.

**Q: Will my code break?**
A: No! Old endpoints continue working until June 1, 2025. You have plenty of time to migrate.

**Q: How long does migration take?**
A: Usually 5-15 minutes. Just update the URL in your code.

**Q: What if I use multiple formats?**
A: Perfect! The unified endpoint handles all formats automatically.

**Q: Will there be any downtime?**
A: No downtime. Both old and new endpoints work during the transition.

**Q: Do I need a new API key?**
A: No, use your existing API key.

**Q: What about billing?**
A: No changes to pricing or billing.

**Q: Can I test first?**
A: Absolutely! The new endpoint is live now. Test thoroughly before switching.

**Q: What if I need help?**
A: Contact support@gatewayz.ai or join our Discord for migration assistance.

**Q: When exactly do old endpoints stop working?**
A: June 1, 2025 at 11:59 PM UTC.

---

These templates can be customized based on your brand voice and specific user segments.
