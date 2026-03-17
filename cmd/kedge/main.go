package main

import (
	"fmt"
	"os"

	"github.com/openkedge/openkedge/internal/protocol"
	"github.com/spf13/cobra"
)

var rootCmd = &cobra.Command{
	Use:   "kedge",
	Short: "OpenKedge — Kedge your passive cloud into self-protected agentic systems",
	Long: `OpenKedge is the open protocol that transforms passive API-based cloud infrastructure
into self-protected agentic systems that actively care for your resources and collaborate
safely with any DevOps agent.

Think of a "kedge": a small anchor that repositions a massive ship safely.
OpenKedge does the same for your cloud.`,
}

var policy string
var agent string

var enforceCmd = &cobra.Command{
	Use:   "enforce",
	Short: "Intercept a mutation and turn it into a safe agentic action",
	Run: func(cmd *cobra.Command, args []string) {
		fmt.Printf("🔱 OpenKedge v0.1.0\n")
		fmt.Printf("   Keding passive mutation → self-protected agentic system\n")
		fmt.Printf("   Policy : %s\n", policy)
		fmt.Printf("   Agent  : %s\n", agent)

		// Simulate agent context pull + interception
		result := protocol.Intercept("mutation", policy, agent)

		fmt.Printf("\n✅ Result: %s\n", result)
		if result == "BLOCK" {
			fmt.Println("   Reason: Unsafe blast radius detected by agent context")
			os.Exit(1)
		}
	},
}

var planCmd = &cobra.Command{
	Use:   "plan",
	Short: "Analyze mutation with live agent context (docs + stack usage)",
	Run: func(cmd *cobra.Command, args []string) {
		fmt.Println("📋 OpenKedge Plan")
		fmt.Println("   Pulling live docs + stack awareness via agent...")
		fmt.Println("   Risk score: LOW (simulated)")
		fmt.Println("   Recommended: ALLOW")
	},
}

var scanCmd = &cobra.Command{
	Use:   "scan",
	Short: "Scan and register a read-only DevOps agent",
	Run: func(cmd *cobra.Command, args []string) {
		fmt.Printf("🔍 Scanning agent: %s\n", agent)
		fmt.Println("   Agent registered successfully as read-only context provider")
	},
}

func init() {
	rootCmd.PersistentFlags().StringVar(&policy, "policy", "prod", "Policy level (prod/stage/dev)")
	rootCmd.PersistentFlags().StringVar(&agent, "agent", "default", "Read-only DevOps agent endpoint")

	rootCmd.AddCommand(enforceCmd, planCmd, scanCmd)
}

func main() {
	if err := rootCmd.Execute(); err != nil {
		fmt.Println(err)
		os.Exit(1)
	}
}
