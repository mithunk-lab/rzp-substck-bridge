import axios from 'axios'

const api = axios.create({
  // Relative base URL — caught by Vite proxy in dev, direct in prod
  baseURL: import.meta.env.VITE_API_URL || '',
})

// Attach the API key from localStorage to every request
api.interceptors.request.use((config) => {
  const key = localStorage.getItem('bridge_api_key')
  if (key) {
    config.headers.Authorization = `Bearer ${key}`
  }
  return config
})

// On 403: key is invalid or missing — clear it and force re-prompt
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 403) {
      localStorage.removeItem('bridge_api_key')
      window.location.reload()
    }
    return Promise.reject(error)
  }
)

export default api
