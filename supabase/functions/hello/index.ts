import { serve } from "https://deno.land/std@0.131.0/http/server.ts"

serve((req: Request) => {
  return new Response(
    JSON.stringify({ message: "Hello from Supabase Edge Functions!" }),
    {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }
  )
})
