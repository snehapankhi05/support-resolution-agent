/** Mirrors app.schemas.auth.TokenResponse — no user/role fields on the token body. */
export type TokenResponse = {
  access_token: string
  token_type: string
  expires_in: number
}

/** Mirrors app.schemas.auth.MeResponse. Role and actor come only from this payload. */
export type MeResponse = {
  actor: string
  id: string
  email: string
  name: string
  role: string
}

export type LoginCredentials = {
  email: string
  password: string
}
