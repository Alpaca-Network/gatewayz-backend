# Sample Performance Test Results

This document shows expected output from the Google Vertex AI endpoint performance comparison test.

## Test Environment

- **Regional Location**: us-central1
- **Test Date**: 2026-01-18
- **Iterations**: 5 per endpoint
- **Test Prompt**: "Write a short haiku about artificial intelligence."

---

## Model: gemini-3-pro-preview

### Global Endpoint (global)

- **Success Rate**: 100.0%
- **Avg TTFC**: 29.34s
- **Median TTFC**: 28.92s
- **Min/Max TTFC**: 27.15s / 31.48s
- **Std Dev**: 1.67s
- **Avg Total Time**: 29.34s
- **Errors**: None

### Regional Endpoint (us-central1)

- **Success Rate**: 80.0% ⚠️
- **Avg TTFC**: 12.45s
- **Median TTFC**: 12.18s
- **Min/Max TTFC**: 11.34s / 13.92s
- **Std Dev**: 0.95s
- **Avg Total Time**: 12.45s
- **Errors**: 1x "404 Model not found"

### Performance Comparison

- **TTFC Improvement**: +57.6% 🚀
- **Total Time Improvement**: +57.6%
- **Winner**: regional (when available)
- **Recommendation**: Regional endpoint is significantly faster BUT has availability issues. Preview models may not always be available on regional endpoints.

### Analysis

**Key Findings**:
- Regional endpoint shows **57.6% improvement** when successful
- Global endpoint has **100% reliability**
- Regional endpoint had **1 failure** (20% failure rate)
- **Conclusion**: Preview models are **officially global-only** - regional access is inconsistent

**Recommendation**:
- **Use global endpoint** for `gemini-3-pro-preview` in production
- Monitor for official regional endpoint support
- Consider the 180s timeout increase (PR #845) to accommodate global endpoint latency

---

## Model: gemini-2.5-flash-lite

### Global Endpoint (global)

- **Success Rate**: 100.0%
- **Avg TTFC**: 3.84s
- **Median TTFC**: 3.72s
- **Min/Max TTFC**: 3.21s / 4.67s
- **Std Dev**: 0.51s
- **Avg Total Time**: 3.84s
- **Errors**: None

### Regional Endpoint (us-central1)

- **Success Rate**: 100.0%
- **Avg TTFC**: 2.15s ✓
- **Median TTFC**: 2.08s
- **Min/Max TTFC**: 1.87s / 2.56s
- **Std Dev**: 0.27s
- **Avg Total Time**: 2.15s
- **Errors**: None

### Performance Comparison

- **TTFC Improvement**: +44.0% 🚀
- **Total Time Improvement**: +44.0%
- **Winner**: regional
- **Recommendation**: Regional endpoint is faster AND reliable - consider using for production

### Analysis

**Key Findings**:
- Regional endpoint shows **44% improvement**
- Both endpoints have **100% reliability**
- Regional endpoint has **lower variance** (0.27s vs 0.51s stddev)
- **Conclusion**: Standard models benefit significantly from regional endpoints

**Recommendation**:
- **Use regional endpoint** for `gemini-2.5-flash-lite` in production
- Update `_get_model_location()` to default to regional for non-preview models
- Monitor TTFC metrics via Prometheus

---

## Model: gemini-1.5-pro

### Global Endpoint (global)

- **Success Rate**: 100.0%
- **Avg TTFC**: 5.23s
- **Median TTFC**: 5.12s
- **Min/Max TTFC**: 4.78s / 5.89s
- **Std Dev**: 0.43s
- **Avg Total Time**: 5.23s
- **Errors**: None

### Regional Endpoint (us-central1)

- **Success Rate**: 100.0%
- **Avg TTFC**: 2.87s ✓
- **Median TTFC**: 2.79s
- **Min/Max TTFC**: 2.56s / 3.34s
- **Std Dev**: 0.31s
- **Avg Total Time**: 2.87s
- **Errors**: None

### Performance Comparison

- **TTFC Improvement**: +45.1% 🚀
- **Total Time Improvement**: +45.1%
- **Winner**: regional
- **Recommendation**: Regional endpoint is faster AND reliable - consider using for production

### Analysis

**Key Findings**:
- Regional endpoint shows **45.1% improvement**
- Both endpoints have **100% reliability**
- Regional endpoint has **lower variance** (0.31s vs 0.43s stddev)
- **Conclusion**: Mature stable models perform excellently on regional endpoints

**Recommendation**:
- **Use regional endpoint** for `gemini-1.5-pro` in production
- Excellent candidate for default regional routing
- Consider this the baseline for "expected performance"

---

## Overall Summary

### Performance by Endpoint Type

| Model | Global Avg | Regional Avg | Improvement | Reliability |
|-------|-----------|--------------|-------------|-------------|
| gemini-3-pro-preview | 29.34s | 12.45s | +57.6% | 80% ⚠️ |
| gemini-2.5-flash-lite | 3.84s | 2.15s | +44.0% | 100% ✓ |
| gemini-1.5-pro | 5.23s | 2.87s | +45.1% | 100% ✓ |

### Key Insights

1. **Preview Models (gemini-3-*)**:
   - Significant performance gains with regional endpoints (+57.6%)
   - BUT: Reliability issues (80% success rate)
   - **Recommendation**: Stick with global endpoints until officially supported regionally

2. **Standard Models (gemini-2.5-*, gemini-1.5-*)**:
   - Consistent 44-45% performance improvement
   - Perfect reliability (100% success rate)
   - **Recommendation**: Use regional endpoints in production

3. **Variance/Consistency**:
   - Regional endpoints show **lower variance** (more predictable)
   - Global endpoints have higher stddev (less consistent)
   - Regional endpoints provide more **consistent user experience**

### Production Recommendations

#### Immediate Actions

1. **Keep preview models on global endpoints**:
   - `gemini-3-pro-preview` → global
   - Increase timeout to 180s (PR #845)
   - Monitor for official regional support

2. **Move standard models to regional endpoints**:
   - `gemini-2.5-flash-lite` → regional
   - `gemini-1.5-pro` → regional
   - `gemini-1.5-flash` → regional

3. **Update routing logic**:
   ```python
   def _get_model_location(model_name: str) -> str:
       # Preview models (gemini-3-*) use global
       if "gemini-3" in model_name.lower():
           return "global"
       # All other models use regional
       return Config.GOOGLE_VERTEX_LOCATION
   ```

#### Monitoring

Add Grafana alerts:
- **TTFC > 5s for standard models**: Warning
- **TTFC > 10s for any model**: Critical
- **Success rate < 95%**: Critical

#### Future Testing

Re-run this test:
- **Weekly**: Check for preview model regional availability
- **After GCP updates**: Verify performance hasn't regressed
- **Before major releases**: Ensure consistent performance

---

## Cost Implications

### Regional Endpoint Benefits

1. **Reduced Latency**: 44-45% faster responses
2. **Lower Network Costs**: Shorter distances = less egress
3. **Better Resource Utilization**: Faster requests = more throughput

### Estimated Impact

**Assumptions**:
- 1M requests/month
- Avg request: 5s → 2.5s (50% improvement)
- Server cost: $0.10/hour

**Savings**:
- Time saved: 2.5s * 1M = 2,500,000s = 694 hours
- Cost saved: 694 hours * $0.10 = **$69.40/month**
- Additional benefit: **2x throughput capacity**

---

## Conclusion

**Regional endpoints offer significant performance improvements** for standard Gemini models with no reliability tradeoff. Preview models should remain on global endpoints until officially supported regionally.

**Next Steps**:
1. Merge PR #845 (timeout increase for global endpoints)
2. Update routing logic to prefer regional for standard models
3. Monitor TTFC metrics in production
4. Re-test preview models monthly for regional availability
