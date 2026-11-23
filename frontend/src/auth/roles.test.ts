import { describe, expect, it } from 'vitest'

import { homePathFor, isCustomer, isReviewer } from './roles'
import type { MeResponse } from './types'

function me(partial: Partial<MeResponse> & Pick<MeResponse, 'actor' | 'role'>): MeResponse {
  return {
    id: 'id-1',
    email: 'user@example.com',
    name: 'User',
    ...partial,
  }
}

describe('isCustomer', () => {
  it('is true only for actor customer', () => {
    expect(isCustomer(me({ actor: 'customer', role: 'customer' }))).toBe(true)
    expect(isCustomer(me({ actor: 'staff', role: 'reviewer' }))).toBe(false)
  })
})

describe('isReviewer', () => {
  it('accepts staff reviewer and admin', () => {
    expect(isReviewer(me({ actor: 'staff', role: 'reviewer' }))).toBe(true)
    expect(isReviewer(me({ actor: 'staff', role: 'admin' }))).toBe(true)
  })

  it('rejects customers and other staff roles', () => {
    expect(isReviewer(me({ actor: 'customer', role: 'customer' }))).toBe(false)
    expect(isReviewer(me({ actor: 'staff', role: 'agent' }))).toBe(false)
  })
})

describe('homePathFor', () => {
  it('routes by role and falls back to login', () => {
    expect(homePathFor(null)).toBe('/login')
    expect(homePathFor(me({ actor: 'customer', role: 'customer' }))).toBe('/')
    expect(homePathFor(me({ actor: 'staff', role: 'reviewer' }))).toBe('/review')
    expect(homePathFor(me({ actor: 'staff', role: 'admin' }))).toBe('/review')
    expect(homePathFor(me({ actor: 'staff', role: 'agent' }))).toBe('/login')
  })
})
