output "vpc_id" {
  value = aws_vpc.main.id
}

output "bastion_public_ip" {
  description = "Bastion public IP"
  value       = aws_eip.bastion.public_ip
}

output "bastion_ssh_command" {
  description = "SSH into the bastion"
  value       = "ssh -i ${local_sensitive_file.bastion_key.filename} ec2-user@${aws_eip.bastion.public_ip}"
}

output "rds_endpoint" {
  description = "RDS endpoint (host:port)"
  value       = aws_db_instance.main.endpoint
}

output "rds_address" {
  description = "RDS hostname"
  value       = aws_db_instance.main.address
}

output "db_tunnel_command" {
  description = "Open a local tunnel to Postgres through the bastion, then psql to localhost:5432"
  value       = "ssh -i ${local_sensitive_file.bastion_key.filename} -N -L 5432:${aws_db_instance.main.address}:5432 ec2-user@${aws_eip.bastion.public_ip}"
}
