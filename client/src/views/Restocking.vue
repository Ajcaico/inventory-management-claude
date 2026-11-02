<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else-if="candidates.length === 0" class="card empty-state">
      {{ t('restocking.noCandidates') }}
    </div>
    <div v-else>
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.budget.title') }}</h3>
        </div>
        <div class="budget-value" aria-live="polite">{{ formatMoney(budget) }}</div>
        <input
          id="budget-slider"
          v-model.number="budget"
          class="budget-slider"
          type="range"
          min="0"
          :max="maxBudget"
          :step="BUDGET_STEP"
          :aria-label="t('restocking.budget.title')"
        />
        <div class="slider-scale">
          <span>{{ formatMoney(0) }}</span>
          <span>{{ t('restocking.budget.max') }}: {{ formatMoney(maxBudget) }}</span>
        </div>
        <p class="help-text">{{ t('restocking.budget.help') }}</p>
      </div>

      <div class="summary-grid">
        <div class="summary-card">
          <div class="stat-label">{{ t('restocking.summary.budget') }}</div>
          <div class="summary-value">{{ formatMoney(budget) }}</div>
        </div>
        <div class="summary-card info">
          <div class="stat-label">{{ t('restocking.summary.spent') }}</div>
          <div class="summary-value">{{ formatMoney(totalCost) }}</div>
        </div>
        <div class="summary-card success">
          <div class="stat-label">{{ t('restocking.summary.remaining') }}</div>
          <div class="summary-value">{{ formatMoney(remainingBudget) }}</div>
        </div>
        <div class="summary-card warning">
          <div class="stat-label">{{ t('restocking.summary.leadTime') }}</div>
          <div class="summary-value">{{ recommendations.length ? t('restocking.days', { count: orderLeadTime }) : '-' }}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommended') }} ({{ recommendations.length }})</h3>
          <button
            type="button"
            class="place-order-btn"
            :disabled="recommendations.length === 0 || submitting"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.placingOrder') : t('restocking.placeOrder') }}
          </button>
        </div>

        <div v-if="orderSuccess" class="message success-message" role="status">{{ orderSuccess }}</div>
        <div v-if="orderError" class="message error-message" role="alert">{{ orderError }}</div>

        <div v-if="recommendations.length === 0" class="empty-state">
          {{ t('restocking.noRecommendations') }}
        </div>
        <div v-else class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.itemName') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th class="num">{{ t('restocking.table.quantity') }}</th>
                <th class="num">{{ t('restocking.table.unitCost') }}</th>
                <th class="num">{{ t('restocking.table.lineCost') }}</th>
                <th class="num">{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="rec in recommendations" :key="rec.sku">
                <td><strong>{{ rec.sku }}</strong></td>
                <td>{{ translateProductName(rec.name) }}</td>
                <td>
                  <span :class="['badge', rec.trend]">{{ t(`trends.${rec.trend}`) }}</span>
                </td>
                <td class="num">
                  <strong>{{ rec.quantity.toLocaleString() }}</strong>
                  <span v-if="rec.partial" class="badge warning partial-badge">{{ t('restocking.partial') }}</span>
                </td>
                <td class="num">{{ formatMoney(rec.unit_cost) }}</td>
                <td class="num"><strong>{{ formatMoney(rec.lineCost) }}</strong></td>
                <td class="num">{{ t('restocking.days', { count: rec.lead_time_days }) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <p v-if="partialItems.length" class="note">
          {{ t('restocking.partialNote', { items: partialText }) }}
        </p>
        <p v-if="unfundedItems.length" class="note">
          {{ t('restocking.unfundedNote', { items: unfundedText }) }}
        </p>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency } from '../utils/currency'

const BUDGET_STEP = 100

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, translateProductName } = useI18n()

    const loading = ref(true)
    const error = ref(null)
    const forecasts = ref([])
    const inventoryItems = ref([])
    const budget = ref(0)

    const submitting = ref(false)
    const orderSuccess = ref('')
    const orderError = ref('')

    const formatMoney = (amount) => formatCurrency(amount, currentCurrency.value)

    // Stock per SKU (sum defensively); SKUs missing from inventory count as 0
    const stockBySku = computed(() => {
      const map = new Map()
      for (const item of inventoryItems.value) {
        map.set(item.sku, (map.get(item.sku) || 0) + (Number(item.quantity_on_hand) || 0))
      }
      return map
    })

    // Non-decreasing forecasts that actually need stock, sorted by growth % (desc)
    const candidates = computed(() => {
      return forecasts.value
        .filter(f => f.trend !== 'decreasing')
        .map(f => {
          const onHand = stockBySku.value.get(f.item_sku) || 0
          const needed = f.forecasted_demand - onHand
          const growth = f.current_demand > 0
            ? (f.forecasted_demand - f.current_demand) / f.current_demand
            : (f.forecasted_demand > 0 ? Infinity : 0)
          return {
            id: f.id,
            sku: f.item_sku,
            name: f.item_name,
            trend: f.trend,
            unit_cost: Number(f.unit_cost) || 0,
            lead_time_days: f.lead_time_days,
            needed,
            growth
          }
        })
        .filter(c => c.needed > 0 && c.unit_cost > 0)
        .sort((a, b) => b.growth - a.growth)
    })

    // Cost of fully funding every candidate, rounded up to the slider step
    const maxBudget = computed(() => {
      const total = candidates.value.reduce((sum, c) => sum + c.needed * c.unit_cost, 0)
      return Math.ceil(total / BUDGET_STEP) * BUDGET_STEP
    })

    // Greedy allocation. Money is tracked in integer cents to avoid float drift.
    const allocation = computed(() => {
      let remainingCents = Math.round(budget.value * 100)
      const recommended = []
      const partial = []
      const unfunded = []

      for (const c of candidates.value) {
        const unitCents = Math.round(c.unit_cost * 100)
        let qty = 0
        if (c.needed * unitCents <= remainingCents) {
          qty = c.needed
        } else {
          qty = Math.floor(remainingCents / unitCents)
        }

        if (qty >= 1) {
          const lineCents = qty * unitCents
          remainingCents -= lineCents
          const rec = {
            sku: c.sku,
            name: c.name,
            trend: c.trend,
            quantity: qty,
            unit_cost: c.unit_cost,
            lead_time_days: c.lead_time_days,
            lineCost: lineCents / 100,
            needed: c.needed,
            partial: qty < c.needed
          }
          recommended.push(rec)
          if (rec.partial) partial.push(rec)
        } else {
          unfunded.push(c)
        }
      }

      return { recommended, partial, unfunded, remaining: remainingCents / 100 }
    })

    const recommendations = computed(() => allocation.value.recommended)
    const partialItems = computed(() => allocation.value.partial)
    const unfundedItems = computed(() => allocation.value.unfunded)
    const remainingBudget = computed(() => allocation.value.remaining)

    const totalCost = computed(() => {
      return Math.round(recommendations.value.reduce((sum, r) => sum + Math.round(r.lineCost * 100), 0)) / 100
    })

    const orderLeadTime = computed(() => {
      return recommendations.value.reduce((max, r) => Math.max(max, Number(r.lead_time_days) || 0), 0)
    })

    const partialText = computed(() => {
      return partialItems.value
        .map(r => `${r.sku} (${r.quantity.toLocaleString()} / ${r.needed.toLocaleString()})`)
        .join(', ')
    })

    const unfundedText = computed(() => {
      return unfundedItems.value
        .map(c => `${c.sku} (${c.needed.toLocaleString()})`)
        .join(', ')
    })

    const loadData = async () => {
      try {
        loading.value = true
        error.value = null
        const [forecastsData, inventoryData] = await Promise.all([
          api.getDemandForecasts(),
          api.getInventory()
        ])
        forecasts.value = forecastsData
        inventoryItems.value = inventoryData
        // Default to about half of full funding, snapped to the slider step
        budget.value = Math.round((maxBudget.value / 2) / BUDGET_STEP) * BUDGET_STEP
      } catch (err) {
        error.value = t('restocking.loadError', { message: err.message })
        console.error('Restocking load error:', err)
      } finally {
        loading.value = false
      }
    }

    const placeOrder = async () => {
      if (submitting.value || recommendations.value.length === 0) return
      submitting.value = true
      orderSuccess.value = ''
      orderError.value = ''
      try {
        const result = await api.createRestockingOrder({
          items: recommendations.value.map(r => ({
            sku: r.sku,
            name: r.name,
            quantity: r.quantity,
            unit_cost: r.unit_cost,
            lead_time_days: r.lead_time_days
          })),
          budget: budget.value
        })
        orderSuccess.value = t('restocking.orderPlaced', {
          orderNumber: result.order_number,
          days: result.lead_time_days
        })
      } catch (err) {
        const detail = err.response && err.response.data && err.response.data.detail
        if (typeof detail === 'string' && detail) {
          orderError.value = detail
        } else if (Array.isArray(detail) && detail.length) {
          // FastAPI 422 validation errors arrive as a list of { msg, ... }
          orderError.value = detail.map(d => (d && d.msg) || '').filter(Boolean).join('; ') || t('restocking.orderFailed')
        } else {
          orderError.value = t('restocking.orderFailed')
        }
        console.error('Place order error:', err)
      } finally {
        submitting.value = false
      }
    }

    onMounted(loadData)

    return {
      t,
      BUDGET_STEP,
      loading,
      error,
      budget,
      maxBudget,
      candidates,
      recommendations,
      partialItems,
      unfundedItems,
      partialText,
      unfundedText,
      totalCost,
      remainingBudget,
      orderLeadTime,
      submitting,
      orderSuccess,
      orderError,
      formatMoney,
      translateProductName,
      placeOrder
    }
  }
}
</script>

<style scoped>
.budget-value {
  font-size: 2.25rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.025em;
  margin-bottom: 1rem;
}

.budget-slider {
  width: 100%;
  accent-color: #2563eb;
  cursor: pointer;
}

.slider-scale {
  display: flex;
  justify-content: space-between;
  margin-top: 0.5rem;
  font-size: 0.813rem;
  color: #64748b;
}

.help-text {
  margin-top: 0.75rem;
  font-size: 0.875rem;
  color: #64748b;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 1.25rem;
  margin-bottom: 1.25rem;
}

.summary-card {
  background: white;
  padding: 1.25rem;
  border-radius: 10px;
  border: 1px solid #e2e8f0;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}

.summary-value {
  font-size: 1.75rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.025em;
}

.summary-card.info .summary-value {
  color: #2563eb;
}

.summary-card.success .summary-value {
  color: #059669;
}

.summary-card.warning .summary-value {
  color: #ea580c;
}

.num {
  text-align: right;
}

.partial-badge {
  margin-left: 0.5rem;
}

.place-order-btn {
  padding: 0.625rem 1.25rem;
  background: #2563eb;
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 0.938rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s ease;
}

.place-order-btn:hover:not(:disabled) {
  background: #1d4ed8;
}

.place-order-btn:disabled {
  background: #cbd5e1;
  cursor: not-allowed;
}

.message {
  padding: 0.75rem 1rem;
  border-radius: 8px;
  margin-bottom: 1rem;
  font-size: 0.938rem;
}

.success-message {
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  color: #065f46;
}

.error-message {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #991b1b;
}

.note {
  margin-top: 1rem;
  padding: 0.75rem 1rem;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  font-size: 0.875rem;
  color: #475569;
}

.empty-state {
  text-align: center;
  padding: 2rem;
  color: #64748b;
  font-size: 0.938rem;
}
</style>
