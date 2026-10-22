defmodule My.MixProject do
  def project, do: [app: :my_app]
  defp deps do
    [{:phoenix, "~> 1.8"}, {:ecto_sql, "~> 3.12"}, {:postgrex, ">= 0.0.0"}]
  end
end
