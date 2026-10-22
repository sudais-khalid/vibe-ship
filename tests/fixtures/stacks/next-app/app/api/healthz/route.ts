export async function GET(){ return Response.json({ok:true, s: process.env.STRIPE_SECRET_KEY}) } // "/healthz"
