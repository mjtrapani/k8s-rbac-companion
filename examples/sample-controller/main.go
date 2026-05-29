// Command sample-controller is a representative Kubernetes workload used to
// demonstrate k8s-rbac-companion. It is a READ TARGET for the plugin's
// discovery pass: point `/k8s-rbac-companion:rule examples/sample-controller`
// at this directory and the plugin infers the least-privilege RBAC its
// ServiceAccount needs from the client-go calls below.
//
// (Building/running it would need `go mod tidy` and an in-cluster context;
// that is not required to generate the rule — the plugin reads the source.)
package main

import (
	"context"
	"fmt"
	"os"

	coordinationv1 "k8s.io/api/coordination/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
)

// namespace returns the controller's own namespace from the downward API
// (POD_NAMESPACE is injected via the Deployment env). Operating in its own
// namespace is what makes this workload a candidate for a namespaced Role.
func namespace() string {
	if ns := os.Getenv("POD_NAMESPACE"); ns != "" {
		return ns
	}
	return "sample-system"
}

func main() {
	cfg, err := rest.InClusterConfig()
	if err != nil {
		panic(err)
	}
	cs, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		panic(err)
	}
	if err := reconcile(context.Background(), cs, namespace()); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

// reconcile exercises the workload's full Kubernetes API surface once.
func reconcile(ctx context.Context, cs *kubernetes.Clientset, ns string) error {
	// Leader-election lock on a Lease before doing work
	// (coordination.k8s.io leases: get + create + update).
	if err := acquireLease(ctx, cs, ns); err != nil {
		return err
	}

	// List + watch pods in our namespace to drive reconciliation
	// (core pods: list + watch).
	// TODO: also watch ConfigMaps so config changes trigger a live reload.
	pods, err := cs.CoreV1().Pods(ns).List(ctx, metav1.ListOptions{})
	if err != nil {
		return err
	}
	if _, err := cs.CoreV1().Pods(ns).Watch(ctx, metav1.ListOptions{}); err != nil {
		return err
	}

	// Read one pod's logs (pods/log subresource: get).
	for _, p := range pods.Items {
		_ = cs.CoreV1().Pods(ns).GetLogs(p.Name, &corev1.PodLogOptions{})
		break
	}

	// Read a specific, named ConfigMap and Secret. Because these target a
	// literal object name with a get verb, the rule can tighten them to
	// resourceNames-scoped grants rather than blanket read on the type.
	if _, err := cs.CoreV1().ConfigMaps(ns).Get(ctx, "sample-controller-config", metav1.GetOptions{}); err != nil {
		return err
	}
	if _, err := cs.CoreV1().Secrets(ns).Get(ctx, "sample-controller-tls", metav1.GetOptions{}); err != nil {
		return err
	}

	// List Deployments and scale the "web" Deployment
	// (apps deployments: list; deployments/scale subresource: get + update).
	if _, err := cs.AppsV1().Deployments(ns).List(ctx, metav1.ListOptions{}); err != nil {
		return err
	}
	scale, err := cs.AppsV1().Deployments(ns).GetScale(ctx, "web", metav1.GetOptions{})
	if err != nil {
		return err
	}
	scale.Spec.Replicas = 3
	if _, err := cs.AppsV1().Deployments(ns).UpdateScale(ctx, "web", scale, metav1.UpdateOptions{}); err != nil {
		return err
	}

	// Record an Event for what happened (core events: create).
	_, err = cs.CoreV1().Events(ns).Create(ctx, newEvent(ns), metav1.CreateOptions{})
	return err
}

// acquireLease implements a minimal leader-election lock on a Lease object.
func acquireLease(ctx context.Context, cs *kubernetes.Clientset, ns string) error {
	const name = "sample-controller-leader"
	lease, err := cs.CoordinationV1().Leases(ns).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		_, cerr := cs.CoordinationV1().Leases(ns).Create(ctx, &coordinationv1.Lease{
			ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: ns},
		}, metav1.CreateOptions{})
		return cerr
	}
	_, err = cs.CoordinationV1().Leases(ns).Update(ctx, lease, metav1.UpdateOptions{})
	return err
}

func newEvent(ns string) *corev1.Event {
	return &corev1.Event{
		ObjectMeta: metav1.ObjectMeta{GenerateName: "sample-controller-", Namespace: ns},
		Reason:     "Reconciled",
		Message:    "sample-controller completed a reconcile pass",
		Type:       corev1.EventTypeNormal,
	}
}
